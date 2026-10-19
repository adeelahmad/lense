"""What automations can do (docs/automations.md, Contract: Actions and intents): one catalog of actions, each
declaring its parameters, its output and its effect, which the canvas, the builder and agents all read.

An action's effect says how far it reaches, and so whether it may run without asking (`resolve_risk`):

- `read` looks things up and changes nothing (a tool's own rules ask for any change it makes): routine.
- `self` tells or keeps something for the automation's own person only: routine.
- `lens` changes something inside Lens: routine on the event's own object in the automation's namespaces, else ask.
- `out` sends outside Lens, reaches other people, or deletes: ask, unless the person set that node to run on its own.

When an action node runs, it renders its params and makes an intent (automation_spec.check_intent); `perform` carries
an intent out, or with `dry` says what it would do and changes nothing. An action acts for the automation's person
(`person`), with their permissions as they are when it runs (`reach`), and what it writes is by the automation itself
(`actor`). Params marked `"format": "prompt"` are prompts: the engine renders them with `render_prompt`, which wraps
what the run read from outside Lens as data; such a param may name a saved prompt template ({"template": 5}) instead.
`log_line` ships here; the other actions are registered by a module per group, loaded with this one: actions_messages.py (notify), actions_circles.py (share_with_circle), actions_ai.py (ask_model, call_tool, ask_agent),
actions_web.py (http_request), actions_run.py (run_workflow, run_pipeline, run_routine), actions_lens.py (set_field, add_to_collection,
write_note), variables.py (set_variable).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from . import auth, store, templates
from . import automation_spec as spec_

NAME_RX = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
GROUPS = ("Messages", "Lens", "Assets", "Web", "AI", "Run")
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
TEMPLATE = "{{"
PROMPT = "prompt"  # the JSON Schema format of a param that is a prompt (render_prompt)
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
        if p.get("format") == PROMPT and isinstance(v, dict):
            try:
                templates.reference(v)  # a saved prompt template in place of the text
            except templates.TemplateProblem as e:
                raise ValueError(f"{spec.name}: {k}: {e}") from None
        elif not _templated(v):
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


def person(db, intent, user=None):
    """The account an intent acts for, as it is now ({id, email, name, admin}): perform's `user`, else the intent's
    run_as. ValueError when there's none, or it has been switched off or removed: an automation never acts for nobody."""
    uid = user.get("id") if isinstance(user, dict) else user
    if uid is None:
        uid = intent.get("run_as")
    try:
        a = auth.get_account(db, int(uid)) if uid is not None else None
    except (TypeError, ValueError):
        a = None
    if not a or a.get("disabled") or a.get("removed_at"):
        raise ValueError("this automation acts for nobody who can sign in: give it a person to run as")
    return {"id": a["id"], "email": a["email"], "name": a.get("name") or a["email"], "admin": bool(a.get("admin"))}


def actor(db, intent):
    """Who an intent's changes are by: the automation itself, {id: automation:<id>, email: "automation: <name>",
    name}, as import hooks act as import_hook:<id>; a draft's dry run is automation:draft."""
    aid = intent.get("automation")
    if aid is None:
        return {"id": "automation:draft", "email": "automation: draft", "name": "Draft"}
    try:
        name = (db.one("SELECT name FROM $r", r=store.R("automation", int(aid))) or {}).get("name")
    except Exception:  # noqa: BLE001 - automations aren't stored yet, or the row is gone: named by its id
        name = None
    name = name or f"Automation {aid}"
    return {"id": f"automation:{aid}", "email": f"automation: {name}", "name": name}


def reach(db, intent, account):
    """(readable, editable): the namespaces an intent may read and change, the person's roles now within the
    automation's namespaces (all of the person's when it names none)."""
    roles = auth.roles(db, account)
    spaces = set(intent.get("spaces") or [])
    readable = {s for s in roles if not spaces or s in spaces}
    return readable, {s for s in readable if auth.allows(roles, s, "editor")}


def allowed(db, account, space, perm):
    """Whether the person's role in a namespace now has a permission (roles.py: edit, process, …)."""
    return perm in auth.permissions(db, account).get(space, ())


# ---------- the asset an action is about ----------
EVENT_OBJECT = "{{ event.object.id }}"
ASSET_RX = re.compile(r"^(?:lens:recording/)?(\d{1,12})$")


def asset_id(v):
    """An asset's id from a param: an id, or an activity's object id (lens:recording/<id>). ValueError otherwise."""
    m = ASSET_RX.match(str("" if v is None else v).strip())
    if not m:
        raise ValueError(f"{v} isn't an asset: give an asset id, or run it on an activity about an asset")
    return int(m.group(1))


def asset_subject(p, key="asset"):
    """The object an action on an asset changes, as activities name it, for resolve_risk; None when it can't tell."""
    try:
        return f"lens:recording/{asset_id(p.get(key))}"
    except ValueError:
        return None


def asset_param(title="Asset"):
    return {"title": title, "default": EVENT_OBJECT, "description": "an asset id, or the activity's object (lens:recording/<id>)"}


def on_asset(p, key="asset"):
    """The asset an action names, in words."""
    a = p.get(key)
    return "the asset it's about" if a in (None, EVENT_OBJECT) else f"asset {a}"


def asset_for(db, intent, account, rid, perm):
    """The asset {id, space, title, collection}, when it is in one of the automation's namespaces and the person's
    role there now has `perm`. ValueError naming the problem."""
    rec = db.one("SELECT space, title, collection FROM $r", r=store.R("recording", int(rid)))
    if not rec:
        raise ValueError(f"there's no asset {rid}")
    if intent["spaces"] and rec["space"] not in intent["spaces"]:
        raise ValueError(f"asset {rid} isn't in this automation's namespaces")
    if not allowed(db, account, rec["space"], perm):
        raise ValueError(f"{account['name']} can't {perm} assets in that namespace")
    return {"id": int(rid), **rec}


# ---------- prompts ----------
def prompt_params(spec):
    """The names of an action's params that are prompts (format "prompt")."""
    return [k for k, p in (spec.params.get("properties") or {}).items() if p.get("format") == PROMPT]


def render_prompt(text, ctx, cfg=None, db=None):
    """A prompt param rendered against the run context (variables.prompt), with what the run read from outside Lens
    marked as data first (untrusted.wrap): each activity's `lens.untrusted` paths, everything earlier steps passed on
    (a model's reply, a web page) and saved variables. The person's own words stay as written. `text` may name a saved
    prompt template ({"template": 5, "version": 2}), read with `db`. ValueError naming the problem."""
    try:
        return variables.prompt(text, ctx, None, cfg, db)["text"]
    except ValueError as e:
        raise ValueError(f"the prompt: {e}") from None


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


from . import actions_ai, actions_circles, actions_lens, actions_messages, actions_run, actions_web, variables  # noqa: E402, F401 - they register their actions; loaded with the catalog
