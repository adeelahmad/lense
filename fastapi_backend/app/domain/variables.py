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

Saved variables keep values between runs ("last alert at", a counter): `automation_var` rows, an automation's own or
its namespace's (`get_vars`, `set_var`), read as `vars.<name>` and written by the `set_variable` action. A value is
JSON up to 16 KB; a list kept by `append` holds the last 100 items. A variable set from outside text is marked so
(`untrusted`): prompts mark it as data and the intents that read it as outside text.

The filters templates have for times, text and addresses (date, ago, local, plain, emails, defang) are in templates.py,
so every template in Lens has them.
"""

from __future__ import annotations

import copy
import datetime as dt
import difflib
import hashlib
import json
import re

from jinja2 import TemplateSyntaxError, Undefined, nodes
from jinja2.exceptions import SecurityError, UndefinedError

from . import actions, auth, automation_spec, schedule, store, templates, untrusted
from . import events as ev

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
R = store.R
VAR_RX = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
OPS = ("set", "increment", "append", "clear")
MAX_BYTES = 16 * 1024  # a value, as JSON
MAX_ITEMS = 100  # what append keeps
MAX_VARS = 100  # in one automation, or one namespace
TRIES = 5  # a write that loses a race with another run reads the value again


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
        "vars": get_vars(db, automation.get("id"), sid),
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
    if ctx.get("steps"):
        out.append("steps")
    saved = ctx.get("vars")
    if isinstance(saved, Vars):
        out += [f"vars.{n}" for n in sorted(saved._outside) if n in saved]
    elif saved:
        out.append("vars")  # where they came from isn't known: all of them
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


# ---------- saved variables ----------
class Vars(dict):
    """Saved variables by name, as the run context has them; `_outside` names those set from outside text."""

    def __init__(self, values=(), outside=()):
        super().__init__(values)
        self._outside = frozenset(outside)


def check_name(name):
    if not isinstance(name, str) or not VAR_RX.match(name):
        raise ValueError(f"a variable is named with lowercase letters, digits and _, starting with a letter, not {str(name)[:60]!r}")
    return name


def check_value(value):
    try:
        size = len(json.dumps(value, ensure_ascii=False).encode())
    except (TypeError, ValueError):
        raise ValueError("a variable holds JSON: text, a number, true or false, a list or an object") from None
    if size > MAX_BYTES:
        raise ValueError(f"a variable holds up to {MAX_BYTES // 1024} KB, not {size // 1024} KB")
    return value


def _scope(automation, space):
    if (automation is None) == (space is None):
        raise ValueError("a variable is an automation's or a namespace's")
    return ("automation", int(automation)) if automation is not None else ("space", int(space))


def _rid(name, automation=None, space=None):
    kind, n = _scope(automation, space)
    return R("automation_var", hashlib.sha1(f"{kind}:{n}:{name}".encode()).hexdigest()[:24])


def _where(automation, space):
    kind, n = _scope(automation, space)
    return ("automation = $n AND space = NONE", n) if kind == "automation" else ("space = $n AND automation = NONE", n)


def list_vars(db, automation=None, space=None):
    """An automation's variables (`automation`) or a namespace's (`space`): [{name, value, untrusted, updated_at,
    updated_by}], by name."""
    where, n = _where(automation, space)
    rows = db.rows(f"SELECT * FROM automation_var WHERE {where}", n=n)  # `value` is a SurrealQL keyword
    keep = ("name", "value", "updated_at", "updated_by")
    return sorted(({**{k: r.get(k) for k in keep}, "untrusted": bool(r.get("untrusted"))} for r in rows), key=lambda r: r["name"])


def get_vars(db, automation=None, space=None):
    """What a run reads as `vars`: the namespace's variables, then the automation's own (which win on a name), as Vars."""
    rows = []
    if space is not None:
        rows += list_vars(db, space=space)
    if automation is not None:
        rows += list_vars(db, automation=automation)
    values, outside = {}, set()
    for r in rows:
        values[r["name"]] = r.get("value")
        outside.discard(r["name"])
        if r["untrusted"]:
            outside.add(r["name"])
    return Vars(values, outside)


def _next(op, old, value, name):
    if op == "set":
        return value
    if op == "increment":
        by = 1 if value is None else value
        if not isinstance(by, (int, float)) or isinstance(by, bool):
            raise ValueError(f"increment {name} by a number, not {str(by)[:40]!r}")
        if old is None:
            return by
        if not isinstance(old, (int, float)) or isinstance(old, bool):
            raise ValueError(f"{name} holds {type(old).__name__}, not a number, so it can't be incremented")
        return old + by
    if op == "append":
        items = [] if old is None else old if isinstance(old, list) else [old]
        return [*items, value][-MAX_ITEMS:]
    raise ValueError(f"the operation is one of {', '.join(OPS)}")


def set_var(db, name, value=None, automation=None, space=None, op="set", by=None, untrusted=False):
    """Change one saved variable, an automation's or a namespace's: `set` it to `value`, `increment` it by `value`
    (default 1), `append` `value` to it (a list of the last 100), or `clear` it. Returns {name, value, previous}
    (value None once cleared). A write that races another run's reads the value again; ValueError naming a problem."""
    check_name(name)
    if op not in OPS:
        raise ValueError(f"the operation is one of {', '.join(OPS)}")
    rid = _rid(name, automation, space)
    for _ in range(TRIES):
        row = db.one("SELECT * FROM $r", r=rid)
        old = (row or {}).get("value")
        if op == "clear":
            if row is None or db.rows("DELETE $r WHERE rev = $rev RETURN BEFORE", r=rid, rev=row.get("rev")):
                return {"name": name, "value": None, "previous": old}
            continue
        new = check_value(_next(op, old, value, name))
        fields = {"value": new, "untrusted": bool(untrusted), "updated_at": store.now(), "updated_by": by}
        if row is None:
            where, n = _where(automation, space)
            if len(db.values(f"SELECT VALUE id FROM automation_var WHERE {where} LIMIT {MAX_VARS}", n=n)) >= MAX_VARS:
                raise ValueError(f"there are {MAX_VARS} variables already; clear one first")
            kind, n = _scope(automation, space)
            content = {
                **fields,
                "name": name,
                "rev": 1,
                "automation": n if kind == "automation" else None,
                "space": n if kind == "space" else None,
            }
            try:
                db.q("CREATE $r CONTENT $d", r=rid, d=store.clean(content))
            except Exception:  # noqa: BLE001 - another run made it first: read it again
                continue
        elif not db.rows(
            "UPDATE $r MERGE $d WHERE rev = $rev RETURN AFTER", r=rid, d={**fields, "rev": row.get("rev", 0) + 1}, rev=row.get("rev")
        ):
            continue
        return {"name": name, "value": new, "previous": old}
    raise ValueError(f"{name} is being changed by other runs; try again")


def put_vars(db, values, automation=None, space=None, by=None):
    """Set several variables at once, as a person does ({name: value}, None clears one); the variables as they are now."""
    if not isinstance(values, dict):
        raise ValueError("give the variables as {name: value}, null to clear one")
    for name, value in values.items():
        check_name(name)
        if value is not None:
            check_value(value)
    for name, value in values.items():
        set_var(db, name, value, automation, space, "clear" if value is None else "set", by)
    return list_vars(db, automation, space)


def manages(db, aid, admin=False, editable=()):
    """Whether someone who can edit `editable` (namespace ids; an admin every one) may see and change automation `aid`
    and its variables: admins any, others one that works only in namespaces they can edit. False when there's none."""
    try:
        row = db.one("SELECT spaces, removed_at FROM $r", r=R("automation", int(aid)))
    except Exception:  # noqa: BLE001 - automations aren't stored on this install yet
        row = None
    if not row or row.get("removed_at"):
        return False
    return bool(admin) or (row.get("spaces") is not None and set(row["spaces"]) <= set(editable))


# ---------- the set_variable action ----------
def _set_variable(db, cfg, intent, user, dry):
    p = intent["params"]
    name, op, value = check_name(p["name"]), p.get("op") or "set", p.get("value")
    account = actions.person(db, intent, user)
    automation, space = intent.get("automation"), None
    if p.get("scope") == "namespace":
        automation, space = None, p.get("namespace")
        if space is None and len(intent["spaces"]) == 1:
            space = intent["spaces"][0]  # the automation's only namespace
        if space is None:
            raise ValueError("set_variable: name the namespace (namespace), or keep it the automation's own")
        space = int(space)
        if intent["spaces"] and space not in intent["spaces"]:
            raise ValueError(f"namespace {space} isn't one of this automation's")
        if not auth.allows(auth.roles(db, account), space, "editor"):
            raise ValueError(f"{account['name']} can't edit that namespace, so its variables stay as they are")
    elif automation is None and not dry:
        raise ValueError("set_variable: a draft keeps no variables; save the automation first")
    if dry:
        old = (get_vars(db, automation, space) if (automation or space) is not None else {}).get(name)
        new = None if op == "clear" else check_value(_next(op, old, value, name))
        return {"name": name, "value": new, "previous": old, "would": describe_set(p)}
    return set_var(db, name, value, automation, space, op, f"automation:{intent.get('automation')}", bool(intent.get("untrusted")))


def describe_set(p):
    name, op, value = p.get("name"), p.get("op") or "set", p.get("value")
    where = "the namespace's" if p.get("scope") == "namespace" else "the automation's"
    shown = json.dumps(value, ensure_ascii=False)[:60] if value is not None else ""
    return {
        "set": f"Set {where} variable {name} to {shown}",
        "increment": f"Add {shown or 1} to {where} variable {name}",
        "append": f"Add {shown} to {where} list {name}",
        "clear": f"Clear {where} variable {name}",
    }.get(op, f"Change {where} variable {name}")


actions.register(
    actions.ActionSpec(
        name="set_variable",
        label="Set a variable",
        group="Run",
        description=(
            "Keeps a value between runs, read as vars.<name>: set it, add to a number (a counter), add to a list (the "
            "last 100), or clear it. The automation's own, or its namespace's to share with its other automations."
        ),
        params={
            "type": "object",
            "properties": {
                "name": {"type": "string", "title": "Variable", "maxLength": 40, "description": "lowercase letters, digits and _"},
                "op": {"type": "string", "title": "Do", "enum": list(OPS), "default": "set"},
                "value": {"title": "Value", "description": "JSON up to 16 KB; for increment a number (default 1)"},
                "scope": {"type": "string", "title": "Whose", "enum": ["automation", "namespace"], "default": "automation"},
                "namespace": {
                    "type": "integer",
                    "title": "Namespace",
                    "description": "for scope namespace: its id (default the automation's only namespace), or {{ namespace.id }}",
                },
            },
        },
        required=("name",),
        effect="self",
        output={
            "type": "object",
            "properties": {"name": {"type": "string"}, "value": {"title": "Value now"}, "previous": {"title": "Value before"}},
        },
        perform=_set_variable,
        describe=describe_set,
    )
)


# ---------- the variable picker and preview ----------
KIND_OF = {"schedule": "clock.tick", "at": "clock.tick", "webhook": "webhook.received"}  # what a trigger's run starts on
SUBKEYS = {  # the run context's own fields, for the picker: (key, label, type)
    "trigger": (("as", "Trigger's name", "string"), ("on", "Trigger's type", "string")),
    "automation": (
        ("id", "Automation's id", "integer"),
        ("name", "Automation's name", "string"),
        ("version", "Version", "integer"),
        ("spaces", "Its namespaces", "array"),
    ),  # fmt: skip
    "run": (("id", "Run's id", "integer"), ("dry", "A try (nothing kept)", "boolean")),
    "namespace": (("id", "Namespace's id", "integer"), ("name", "Namespace's name", "string")),
    "user": (("id", "Person's id", "integer"), ("name", "Person's name", "string"), ("email", "Person's email", "string")),
}
MAX_DEPTH = 5  # how deep the picker goes into an activity's or an output's fields
MAX_LISTED = 500  # entries in one catalog
MAX_EXAMPLE = 120  # characters of an example


def _graph(db, workflow):
    """A draft's graph: {nodes, edges} as given (it may be unfinished), or the saved workflow it names; {} for none."""
    if workflow is None:
        return {}
    if isinstance(workflow, dict):
        return workflow
    if isinstance(workflow, int) and not isinstance(workflow, bool):
        from . import workflows  # imported here: workflows builds on this module, through automation_nodes

        try:
            return workflows.get(db, workflow).get("graph") or {}
        except KeyError:
            raise ValueError(f"there's no workflow {workflow}") from None
    raise ValueError("the draft's workflow is a workflow's id or a graph {nodes, edges}")


def draft_parts(db, draft):
    """A draft automation's triggers and join, checked (automation_spec), and its graph. ValueError naming a problem."""
    if not isinstance(draft, dict):
        raise ValueError("the draft is an automation: {triggers, join?, workflow?, spaces?, id?}")
    triggers = automation_spec.check_triggers(draft.get("triggers"))
    join = automation_spec.check_join(draft.get("join"), [t["as"] for t in triggers])
    return triggers, join, _graph(db, draft.get("workflow"))


def _kinds(t):
    """The kinds of activity a trigger's runs start on (none for a manual one)."""
    return list(t.get("kinds") or []) if t["on"] in ("event", "relative") else [KIND_OF[t["on"]]] if t["on"] in KIND_OF else []


def _sample(t):
    """A trigger's stand-in activity: the sample of the first of its kinds that has one."""
    for k in _kinds(t):
        try:
            got = ev.kind_info(k).get("sample")
        except KeyError:
            continue
        if got:
            return copy.deepcopy(got)
    return {}


def upstream(graph, node):
    """The ids of the nodes before `node` in a graph (what it can read in steps), nearest first; ValueError when the
    graph has no such node."""
    nodes = {n.get("id"): n for n in (graph or {}).get("nodes") or [] if isinstance(n, dict)}
    if node not in nodes:
        raise ValueError(f"the workflow has no node {node}")
    into: dict = {}
    for e in (graph or {}).get("edges") or []:
        if isinstance(e, dict):
            into.setdefault(e.get("target"), []).append(e.get("source"))
    out, todo = [], list(into.get(node, []))
    while todo:
        n = todo.pop(0)
        if n in nodes and n not in out and n != node:
            out.append(n)
            todo += into.get(n, [])
    return out


def _stand_in(schema, path):
    """A value shaped like an output schema, its text naming where it comes from: "[steps.n3.date]"."""
    schema = schema if isinstance(schema, dict) else {}
    if schema.get("properties"):
        return {k: _stand_in(v, f"{path}.{k}") for k, v in schema["properties"].items()}
    return {"array": [], "object": {}, "integer": 1, "number": 1, "boolean": True}.get(_type(schema), f"[{path}]")


def _outputs(graph, ids):
    """{node id: (label, output schema)} for the action and decide nodes among `ids`."""
    nodes = {n.get("id"): n for n in (graph or {}).get("nodes") or [] if isinstance(n, dict)}
    out = {}
    for i in ids:
        n, c = nodes[i], nodes[i].get("config") or {}
        if n.get("type") == "action":
            try:
                spec = actions.get(c.get("action"))
            except KeyError:
                continue
            asked = (c.get("params") or {}).get("json_schema")
            if "json_schema" in (spec.params.get("properties") or {}) and isinstance(asked, dict) and asked.get("properties"):
                out[i] = (n.get("label") or spec.label, asked)  # it gives the object its json_schema describes
            else:
                out[i] = (n.get("label") or spec.label, spec.output or {"type": "object"})
        elif n.get("type") == "decide":
            out[i] = (
                n.get("label") or "Decide",
                {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string", "title": "Branch taken (an option, or unsure)"},
                        "choice": {"type": "string", "title": "Option chosen (its best guess when unsure)"},
                        "confidence": {"type": "number", "title": "Confidence, 0 to 1"},
                        "sure": {"type": "boolean", "title": "Sure enough to follow it"},
                    },
                },
            )
    return out


def sample_context(db, cfg, draft, user, activity=None, trigger=None, node=None, now=None):
    """The run context a draft automation's run would see, for previews and the picker's examples: each trigger's
    activity is its kind's sample, the one that fired (`trigger`, default the first) `activity` when given, earlier
    steps of `node` stand-ins named by their path, and saved variables as they are. `draft` is {triggers, join?,
    workflow? (an id or a graph), spaces?, id?, name?}, already checked against who asks."""
    triggers, join, graph = draft_parts(db, draft)
    alias = trigger or triggers[0]["as"]
    fired = next((t for t in triggers if t["as"] == alias), None)
    if fired is None:
        raise ValueError(f"the draft has no trigger {alias}")
    acts = {t["as"]: (activity if t is fired and activity is not None else _sample(t)) for t in triggers}
    batch = join.get("mode") == "batch"
    automation = {"id": draft.get("id"), "name": draft.get("name") or "Draft", "version": None,
                  "spaces": draft.get("spaces"), "triggers": triggers}  # fmt: skip
    ctx = context(db, cfg, automation, {"id": None, "dry": True}, fired, acts[alias],
                  {a: [x] if batch else x for a, x in acts.items()}, user, now)  # fmt: skip
    if node is not None:
        ups = upstream(graph, node)
        ctx["steps"] = {i: _stand_in(schema, f"steps.{i}") for i, (_label, schema) in _outputs(graph, ups).items()}
    return ctx


def _type(schema):
    t = (schema or {}).get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    return t or ("object" if (schema or {}).get("properties") else "any")


def _kind_of_value(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    return {float: "number", str: "string", list: "array", dict: "object"}.get(type(v), "any")


def _value_at(value, path):
    for part in path.split(".") if path else []:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _example(v):
    """A short example for the picker: text cut to MAX_EXAMPLE characters, small lists and objects as they are."""
    if isinstance(v, str):
        return v if len(v) <= MAX_EXAMPLE else v[: MAX_EXAMPLE - 1] + "…"
    if isinstance(v, (dict, list)):
        return v if len(json.dumps(v, ensure_ascii=False, default=str)) <= MAX_EXAMPLE else None
    return v


def _fields(schema, depth=0, base=""):
    """(path, schema) for every field of a JSON Schema, list items as .0, at most MAX_DEPTH deep."""
    out = []
    schema = schema if isinstance(schema, dict) else {}
    if depth >= MAX_DEPTH:
        return out
    if schema.get("properties"):
        for k, v in schema["properties"].items():
            p = f"{base}.{k}" if base else k
            out.append((p, v))
            out += _fields(v, depth + 1, p)
    elif _type(schema) == "array" and isinstance(schema.get("items"), dict) and schema["items"].get("properties"):
        out += _fields(schema["items"], depth + 1, f"{base}.0")
    return out


def _entry(path, type_, label, example, group, untrusted_=False, description=None):
    out = {"path": path, "type": type_, "label": label, "example": _example(example), "group": group, "untrusted": untrusted_}
    if description:
        out["description"] = description
    return out


def _activity_fields(kinds):
    """[(path, schema, outside)] for the fields every one of `kinds` has (an activity's own `lens` left out)."""
    common = None
    for k in kinds:
        try:
            info = ev.kind_info(k)
        except KeyError:
            continue
        outside = info.get("untrusted") or []
        got = {
            p: (s, any(p == u or p.startswith(u + ".") for u in outside))
            for p, s in _fields(info.get("schema") or {})
            if p != "lens" and not p.startswith("lens.")
        }
        common = got if common is None else {p: v for p, v in common.items() if p in got}
    return [(p, s, o) for p, (s, o) in (common or {}).items()]


def catalog(db, cfg, draft, node=None, user=None, now=None):
    """What a draft automation's templates can read, for the variable picker: [{path, type, label, example, group,
    untrusted, description?}]. The run context's keys; `event` with the fields all its triggers' kinds share and
    `events.<alias>` with each trigger's (a batch's as a list, `.0` its first); the saved variables (the automation's
    own when `draft` has its id, and its namespaces'); and, with `node`, what the action and decide nodes before it
    pass on (`steps.<id>`, from each action's output schema). `untrusted` marks text from outside Lens, which prompts
    mark as data. Examples come from the kinds' samples and the variables' values."""
    triggers, join, graph = draft_parts(db, draft)
    ctx = sample_context(db, cfg, draft, user, None, None, None, now)
    batch = join.get("mode") == "batch"
    out = []
    for key, title, type_, desc in automation_spec.CONTEXT:
        if key == "event":
            out.append(_entry("event", type_, title, None, "Run", False, desc))
            shared: list | None = None
            for t in triggers:
                got = _activity_fields(_kinds(t))
                shared = got if shared is None else [x for x in shared if x[0] in {p for p, _s, _o in got}]
            for p, s, o in shared or []:
                out.append(
                    _entry(f"event.{p}", _type(s), s.get("title") or p.rsplit(".", 1)[-1], _value_at(ctx["event"], p), "The event", o)
                )
        elif key == "events":
            for t in triggers:
                base = f"events.{t['as']}"
                kinds = _kinds(t)
                label = ev.kind_info(kinds[0])["label"] if len(kinds) == 1 else ", ".join(kinds) or "run by hand"
                group = f"Trigger {t['as']}: {label}"
                act = _value_at(ctx["events"], f"{t['as']}.0" if batch else t["as"])
                out.append(_entry(base, "array" if batch else "object", f"{t['as']}'s activit{'ies' if batch else 'y'}", None, group))
                item = f"{base}.0" if batch else base
                for p, s, o in _activity_fields(kinds):
                    out.append(_entry(f"{item}.{p}", _type(s), s.get("title") or p.rsplit(".", 1)[-1], _value_at(act, p), group, o))
        elif key == "vars":
            out.append(_entry("vars", type_, title, None, "Saved variables", False, desc))
            rows = {}
            for s in sorted(draft.get("spaces") or []):
                rows |= {r["name"]: r for r in list_vars(db, space=s) if r["name"] not in rows}
            if draft.get("id") is not None:
                rows |= {r["name"]: r for r in list_vars(db, automation=draft["id"])}
            for name, r in sorted(rows.items()):
                out.append(_entry(f"vars.{name}", _kind_of_value(r["value"]), name, r["value"], "Saved variables", r["untrusted"]))
        elif key == "steps":
            if node is None:
                continue
            out.append(_entry("steps", type_, title, None, "Steps before this one", True, desc))
            for i, (label, schema) in _outputs(graph, upstream(graph, node)).items():
                group = f"Step {i}: {label}"
                out.append(_entry(f"steps.{i}", "object", label, None, group, True))
                for p, s in _fields(schema):
                    out.append(_entry(f"steps.{i}.{p}", _type(s), s.get("title") or p.rsplit(".", 1)[-1], None, group, True))
        else:
            out.append(_entry(key, type_, title, ctx.get(key) if not isinstance(ctx.get(key), dict) else None, "Run", False, desc))
            for sub, label, sub_type in SUBKEYS.get(key, ()):
                out.append(_entry(f"{key}.{sub}", sub_type, label, _value_at(ctx.get(key) or {}, sub), "Run"))
    return out[:MAX_LISTED]


def preview(db, cfg, template, draft, user, activity=None, trigger=None, node=None, as_prompt=False, now=None):
    """What a template gives for a draft automation (sample_context: a real activity or the kind's sample), before it
    is saved: {text, value, prompt, rules, system, error, line, activity}. A param keeps its type in `value` (`text`
    is it written out); a prompt (`as_prompt`, or a saved prompt template {"template": id}) shows outside text marked
    as the model will see it, with `rules`, the system prompt's paragraph about it. A template that fails gives
    `error`, naming the missing variable and its nearest name, and `line`. `activity` is {id, kind, sample}: what it
    was rendered against. A draft that doesn't check raises ValueError."""
    ctx = sample_context(db, cfg, draft, user, activity, trigger, node, now)
    is_prompt = bool(as_prompt) or (isinstance(template, dict) and "template" in template)
    used = ctx["event"] if isinstance(ctx["event"], dict) else {}
    out = {
        "text": None,
        "value": None,
        "prompt": is_prompt,
        "rules": None,
        "system": None,
        "error": None,
        "line": None,
        "activity": {"id": used.get("id") if activity is not None else None, "kind": used.get("kind"), "sample": activity is None},
    }
    try:
        if is_prompt:
            got = prompt(template, ctx, None, cfg, db)
            return {**out, "text": got["text"], "value": got["text"], "rules": got["rules"] or None, "system": got["system"]}
        value = render(template, ctx)
    except ValueError as e:
        return {**out, "error": str(e), "line": getattr(e, "line", None)}
    text_ = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return {**out, "text": text_, "value": value}
