"""Variables and templates for automations (docs/automations.md, Variables and templates).

An automation's params and prompts are templates over its run context: sandboxed Jinja (templates.py) that reads
every trigger's activity, the person, the time and what earlier nodes passed on, joined in one line, like
"{{ events.mail.object.subject }} on {{ events.soon.object.startTime | date('%a %H:%M') }}".

- `context` builds the run context (docs/automations.md, Run context): the activities, the automation, the person,
  `now` in UTC and as `now_local` in the run's `timezone`, and the saved variables in `vars`.
- `render` fills params in: a string that is exactly one `{{ }}` expression keeps its type (a list stays a list),
  others render as text. Errors name the param and the missing variable with the nearest name there is ("text:
  events.mail.object.subjct is not defined; did you mean subject?"). Text from outside Lens keeps its words, but loses
  invisible characters (untrusted.visible), which would smuggle instructions to whatever model reads it next.
- `prompt` renders a prompt with every value read from outside Lens marked as data (untrusted.wrap): each activity's
  `lens.untrusted` fields, what earlier nodes passed on (`steps`) and saved variables (`vars`, which a run may have
  set from outside text). The prompt is inline text or a saved, versioned prompt template ({"template": 5, "version":
  2}), and comes back with the system rules to add (untrusted.rules).

The filters templates have for times, text and addresses (date, ago, local, plain, emails, defang) are in templates.py,
so every template in Lens has them.
"""

from __future__ import annotations

import copy
import datetime as dt
import difflib
import re

from jinja2 import TemplateSyntaxError, Undefined, nodes
from jinja2.exceptions import SecurityError, UndefinedError

from . import events as ev
from . import schedule, store, templates, untrusted

WHOLE_RX = re.compile(r"^\{\{\s*([^{}]+?)\s*\}\}$")
LINE_RX = re.compile(r"^line (\d+): ")
MISSING_RX = re.compile(r"is undefined|has no attribute|has no element|is not defined")
NAMED_RX = re.compile(r"has no (?:attribute|element) '?([^'\s]+)'?|^'([^']+)' is undefined")
OUTSIDE = "outside text"
FROM_STEP = "an earlier step of this automation"
FROM_VAR = "a saved variable"
GUARDS = {"default", "d"}  # filters that stand in for a missing value
OWN = {"loop", "self", "caller", "varargs", "kwargs", "super"}  # names Jinja gives a template itself
TESTS = {"defined", "undefined", "none"}
UTC = dt.timezone.utc


class Problem(ValueError):
    """A template that can't be rendered. The message says where and what; `line` is the template's line, if known."""

    def __init__(self, message, line=None):
        super().__init__(message)
        self.line = line


def templated(v):
    return isinstance(v, str) and ("{{" in v or "{%" in v)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


# ---------- the run context ----------
def _z(d):
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def timezone_of(automation, trigger):
    """The run's time zone: the trigger's, else the first one the automation's triggers name, else UTC."""
    for t in [trigger or {}, *((automation or {}).get("triggers") or [])]:
        tz = t.get("timezone") if isinstance(t, dict) else None
        if tz:
            try:
                schedule.zone(tz)
            except ValueError:
                continue
            return tz
    return "UTC"


def context(db, cfg, automation, run, trigger, event, events, user, now=None):
    """The run context (docs/automations.md, Run context). `automation` is {id, name, version, spaces} (spaces None:
    every namespace; triggers, when given, lend their time zone), `run` {id, dry}, `trigger` {as, on, timezone?},
    `user` the account the automation acts for, `now` when the run started (default now)."""
    automation, trigger = automation or {}, trigger or {}
    spaces = automation.get("spaces")
    names = store.space_names(db)
    here = [s for s in ev.spaces_of(event or {}) if spaces is None or s in spaces]
    sid = here[0] if here else (sorted(spaces)[0] if spaces else None)
    tz = timezone_of(automation, trigger)
    now = now or dt.datetime.now(UTC)
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    return {
        "event": event or {},
        "events": dict(events or {}),
        "trigger": {"as": trigger.get("as"), "on": trigger.get("on")},
        "automation": {
            "id": automation.get("id"),
            "name": automation.get("name"),
            "version": automation.get("version"),
            "spaces": None if spaces is None else sorted(spaces),
        },
        "run": {"id": (run or {}).get("id"), "dry": bool((run or {}).get("dry"))},
        "namespace": {"id": sid, "name": names.get(sid)} if sid is not None else None,
        "user": {"id": (user or {}).get("id"), "name": (user or {}).get("name"), "email": (user or {}).get("email")},
        "now": _z(now),
        "now_local": now.astimezone(schedule.zone(tz)).isoformat(timespec="seconds"),
        "timezone": tz,
        "vars": {},
        "steps": {},
    }


# ---------- what came from outside Lens ----------
def outside_paths(ctx):
    """The run context's paths that hold text from outside Lens: each activity's `lens.untrusted` fields (under `event`
    and `events`, a batch's activities by number), what nodes passed on (`steps`) and the saved variables (`vars`)."""
    out = []

    def of(a, base):
        if isinstance(a, dict):
            out.extend(f"{base}.{p}" for p in (a.get("lens") or {}).get("untrusted") or [])

    of(ctx.get("event"), "event")
    for alias, a in (ctx.get("events") or {}).items():
        if isinstance(a, list):
            for i, x in enumerate(a):
                of(x, f"events.{alias}.{i}")
        else:
            of(a, f"events.{alias}")
    out += [k for k in ("steps", "vars") if ctx.get(k)]
    return out


def _source(ctx, parts):
    """Where the text at a path came from, as a wrapper names it: "<kind> from <actor>" for an activity."""
    a = None
    if parts[0] == "event":
        a = ctx.get("event")
    elif parts[0] == "events" and len(parts) > 1:
        a = (ctx.get("events") or {}).get(parts[1])
        if isinstance(a, list) and len(parts) > 2 and parts[2].isdigit() and int(parts[2]) < len(a):
            a = a[int(parts[2])]
    elif parts[0] == "steps":
        return FROM_STEP
    elif parts[0] == "vars":
        return FROM_VAR
    if not isinstance(a, dict):
        return OUTSIDE
    return f"{a.get('kind') or 'activity'} from {(a.get('actor') or {}).get('name') or 'a source'}"


def _deep(v, fn):
    if isinstance(v, str):
        return fn(v)
    if isinstance(v, list):
        return [_deep(x, fn) for x in v]
    if isinstance(v, dict):
        return {k: _deep(x, fn) for k, x in v.items()}
    return v


def _at(node, parts, fn):
    """Apply fn to every string under the path `parts` of `node` (a copy of ours), copying the containers on the way."""
    k = parts[0]
    if isinstance(node, dict) and k in node:
        key = k
    elif isinstance(node, list) and k.isdigit() and int(k) < len(node):
        key = int(k)
    else:
        return
    child = node[key]
    if len(parts) == 1:
        node[key] = _deep(child, fn)
        return
    if not isinstance(child, (dict, list)):
        return
    node[key] = child = copy.copy(child)
    _at(child, parts[1:], fn)


def apply(ctx, paths, fn):
    """A copy of the run context with fn(text, source) applied to every string under each path; the rest is shared,
    and the context itself is left as it was."""
    out = dict(ctx or {})
    for path in paths or ():
        parts = str(path).split(".")
        source = _source(ctx or {}, parts)
        _at(out, parts, lambda s, source=source: fn(s, source))
    return out


# ---------- explaining what's missing ----------
def _chain(n):
    """The dotted path an expression node reads (event.object.name, steps["n3"]), or None."""
    if isinstance(n, nodes.Name):
        return [n.name]
    if isinstance(n, nodes.Getattr):
        base = _chain(n.node)
        return base and [*base, n.attr]
    if isinstance(n, nodes.Getitem) and isinstance(n.arg, nodes.Const) and isinstance(n.arg.value, (str, int)):
        base = _chain(n.node)
        return base and [*base, str(n.arg.value)]
    return None


def _close(word, options):
    options = [str(o) for o in options]
    got = difflib.get_close_matches(word, options, n=1, cutoff=0.6)
    if got:
        return f"; did you mean {got[0]}?"
    if 0 < len(options) <= 12:
        return f"; it has {', '.join(sorted(options))}"
    return ""


def _first_missing(chain, ctx):
    """(the path up to the part that isn't there, a hint) for a path the context doesn't have, else None."""
    env = templates._env(False, True)
    root = chain[0]
    if root not in ctx:
        return None if root in env.globals else (root, _close(root, ctx))
    cur = ctx[root]
    for i, part in enumerate(chain[1:], 1):
        path = ".".join(chain[: i + 1])
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
                continue
            return None if hasattr(cur, part) else (path, _close(part, cur))
        if isinstance(cur, (list, tuple)):
            if re.fullmatch(r"-?\d+", part) and -len(cur) <= int(part) < len(cur):
                cur = cur[int(part)]
                continue
            return None if hasattr(cur, part) else (path, f"; {'.'.join(chain[:i])} has {len(cur)} items")
        if cur is None:
            return (path, f"; {'.'.join(chain[:i])} is empty")
        return None  # a method or attribute of a value: the sandbox decides
    return None


def missing(source, ctx, expression=False, name=None):
    """(message, line) naming the first variable a template reads that the context doesn't have, or None. Paths
    guarded by `default` or `is defined` don't count, nor names the template sets itself; with `name` (what Jinja
    said is missing) only a path missing that part does."""
    try:
        ast = templates._env(False, True).parse("{{ " + source + " }}" if expression else source)
    except TemplateSyntaxError:
        return None
    guarded = set()
    for f in ast.find_all(nodes.Filter):
        if f.name in GUARDS and f.node is not None:
            guarded |= {id(f.node), *(id(x) for x in f.node.find_all(nodes.Node))}
    for t in ast.find_all(nodes.Test):
        if t.name in TESTS:
            guarded |= {id(t.node), *(id(x) for x in t.node.find_all(nodes.Node))}
    own = OWN | {n.name for n in ast.find_all(nodes.Name) if n.ctx in ("store", "param")}
    for n in ast.find_all((nodes.Getattr, nodes.Getitem, nodes.Name)):
        chain = _chain(n)
        if not chain or id(n) in guarded or chain[0] in own:
            continue
        got = _first_missing(chain, ctx)
        if got and (name is None or got[0].rsplit(".", 1)[-1] == name):
            return f"{got[0]} is not defined{got[1]}", n.lineno
    return None


def _problem(where, message, line=None):
    return Problem(f"{where}: {message}" if where else message, line)


def _explain(where, source, ctx, e, expression=False):
    """A Problem for a template that failed: the missing variable with a hint when that is why, else Jinja's own."""
    message = str(e)
    line = None
    m = LINE_RX.match(message)
    if m:
        line = int(m.group(1))
    elif MISSING_RX.search(message) or isinstance(e, UndefinedError):
        named = NAMED_RX.search(message)
        got = missing(source, ctx, expression, named and (named.group(1) or named.group(2)))
        if got:
            message, line = got
    return _problem(where, message, line)


# ---------- rendering ----------
def text(body, ctx, where=""):
    """A template rendered as text (templates.render_body, kind param: sandboxed, size-limited, no HTML escaping, dict
    keys before dict methods). Problem naming `where` and the missing variable."""
    try:
        return templates.render_body(str(body), ctx, templates.PARAM)
    except templates.TemplateProblem as e:
        raise _explain(where, str(body), ctx, e) from None


def expression(expr, ctx, where=""):
    """One `{{ }}` expression's value, its type kept (a copy, so nothing changes the context)."""
    try:
        got = templates._env(False, True).compile_expression(expr, undefined_to_none=False)(**ctx)
    except SecurityError as e:
        raise _problem(where, f"not allowed in templates: {e}") from None
    except TemplateSyntaxError as e:
        raise _problem(where, e.message or "it isn't a template", 1) from None
    except (UndefinedError, TypeError, ValueError) as e:
        raise _explain(where, expr, ctx, e, expression=True) from None
    if isinstance(got, Undefined):
        raise _explain(where, expr, ctx, UndefinedError(f"{expr.strip()} is not defined"), expression=True)
    if callable(got):
        raise _problem(where, f"{expr.strip()} is a function: call it, like {expr.strip()}()")
    if isinstance(got, str) and len(got) > templates.LIMIT:
        raise _problem(where, "the output is too large")
    return copy.deepcopy(got) if isinstance(got, (dict, list)) else got


def _render(value, ctx, where):
    if isinstance(value, dict):
        return {k: _render(v, ctx, f"{where}.{k}" if where else str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, ctx, f"{where}.{i}" if where else str(i)) for i, v in enumerate(value)]
    if not templated(value):
        return value
    whole = WHOLE_RX.match(value.strip())
    if whole:
        return expression(whole.group(1), ctx, where)
    return text(value, ctx, where)


def render(value, ctx, untrusted_paths=None, where=""):
    """Params filled in from the run context, dicts and lists walked: a string that is exactly one `{{ }}` expression
    keeps its type, others render as text. `untrusted_paths` (default outside_paths) hold text from outside Lens, which
    keeps its words without invisible characters. `where` names the param in errors (Problem, a ValueError)."""
    if not any(templated(s) for s in _strings(value)):
        return value
    paths = outside_paths(ctx) if untrusted_paths is None else untrusted_paths
    return _render(value, apply(ctx, paths, lambda s, _source: untrusted.visible(s)), where)


def prompt(template, ctx, untrusted_paths=None, cfg=None, db=None):
    """A prompt rendered against the run context with every value under `untrusted_paths` (default outside_paths)
    marked as data (untrusted.mark: wrap's markers, its source the activity's kind and actor, kept through filters),
    and the person's own words as written.
    `template` is the text, or a saved prompt template {"template": id, "version": n?} (templates.get). Returns {text,
    rules (the system prompt's rules on marked text, to add), system and schema (the saved template's own), template
    ({id, name, version}, for a saved one)}. Problem naming the problem."""
    saved = _saved(db, template)
    body = saved["body"] if saved else "" if template is None else str(template)
    paths = outside_paths(ctx or {}) if untrusted_paths is None else untrusted_paths
    safe = apply(ctx, paths, lambda s, source: untrusted.mark(s, source, cfg))
    return {
        "text": text(body, safe, ""),
        "rules": untrusted.rules(cfg),
        "system": (saved or {}).get("system"),
        "schema": (saved or {}).get("schema"),
        "template": {"id": saved["id"], "name": saved["name"], "version": saved["version"]} if saved else None,
    }


def _saved(db, value):
    """The saved prompt template (templates.get) a prompt names ({"template": id, "version": n?}), or None for text."""
    try:
        ref = templates.reference(value)
    except templates.TemplateProblem as e:
        raise Problem(str(e)) from None
    if not ref:
        return None
    if db is None:
        raise Problem("a saved template is read from the database")
    try:
        t = templates.get(db, *ref)
    except KeyError:
        raise Problem(f"there's no template {ref[0]}" + (f" version {ref[1]}" if ref[1] else "")) from None
    if t["kind"] != "prompt":
        raise Problem(f"template {ref[0]} ({t['name']}) is a {t['kind']} template, not a prompt")
    return t


def check_prompt(db, value):
    """A prompt param as saved: its template text checks, or the saved prompt template it names is there. ValueError."""
    if _saved(db, value) is None and templated(value):
        templates.check(value)


def fill_prompts(spec, params, ctx, cfg=None, db=None, prompts=()):
    """An action's params with its prompt params (`prompts`) rendered by `prompt`; a saved template's own instructions
    and output schema fill the action's `system` and `json_schema` params when it has them and they weren't given."""
    out = dict(params)
    props = spec.params.get("properties") or {}
    for k in prompts:
        v = out.get(k)
        if not (isinstance(v, str) or isinstance(v, dict)):
            continue
        try:
            got = prompt(v, ctx, None, cfg, db)
        except Problem as e:
            raise Problem(f"{k}: {e}", e.line) from None
        out[k] = got["text"]
        if got["system"] and "system" in props and not out.get("system"):
            out["system"] = got["system"]
        if (got["schema"] or {}).get("properties") and "json_schema" in props and not out.get("json_schema"):
            out["json_schema"] = got["schema"]
    return out
