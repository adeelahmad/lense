"""Match trees (docs/automations.md, Match trees): the one condition language for a trigger's `where`, an
automation's `if`, and the condition, filter and switch nodes of a workflow.

    M := {"all": [M, ...]} | {"any": [M, ...]} | {"not": M} | {"path": "a.b.0", "op": OP, "value": V}

A leaf tests the part of a value at a dotted path (list items by number, as flow.dig does; no path tests the value
itself) with one of the workflow engine's tests (flow.OPS: exists, empty, equals, not_equals, contains, gt, lt), the
same way a condition node does. `all` holds when every tree in it does (an empty one always does), `any` when one
does, `not` when its tree doesn't. Values are literals: templates belong to actions, which keeps matching cheap and
safe. A tree nests at most MAX_DEPTH deep and holds at most MAX_LEAVES tests.

`env` carries what tests about time and state need (`now`, `timezone`, a `state(key)` reader). None of the tests here
reads it; the operators added later do, so that tests and dry runs can fix the clock instead of reading a global one.
"""

from __future__ import annotations

from . import flow

MAX_DEPTH = 6
MAX_LEAVES = 50
GROUPS = ("all", "any", "not")
LEAF = {"path", "op", "value"}
WORDS = {  # each test in words, and its opposite (for a test under not)
    "exists": ("is set", "is not set"),
    "empty": ("is empty", "is not empty"),
    "equals": ("is", "is not"),
    "not_equals": ("is not", "is"),
    "contains": ("contains", "doesn't contain"),
    "gt": ("is more than", "is not more than"),
    "lt": ("is less than", "is not less than"),
}


def check(tree, what="the condition"):
    """The tree, cleaned: ValueError naming the first problem, and where it is (`what`, then the path into the tree,
    such as "the condition, all.1: ...")."""
    return _check(tree, what, 0, [0], "")


def _check(m, what, depth, count, at):
    where = f"{what}, {at}" if at else what
    if depth >= MAX_DEPTH:
        raise ValueError(f"{where}: conditions nest at most {MAX_DEPTH} deep")
    if not isinstance(m, dict) or not m:
        raise ValueError(f"{where}: a condition is {{all: [...]}}, {{any: [...]}}, {{not: ...}} or {{path, op, value}}")
    groups = [k for k in GROUPS if k in m]
    if groups:
        if len(m) != 1:
            raise ValueError(f"{where}: {groups[0]} stands alone, with nothing next to it")
        k = groups[0]
        if k == "not":
            return {"not": _check(m["not"], what, depth + 1, count, f"{at}.not" if at else "not")}
        items = m[k]
        if not isinstance(items, list):
            raise ValueError(f"{where}: {k} is a list of conditions")
        if k == "any" and not items:
            raise ValueError(f"{where}: any needs at least one condition")
        return {k: [_check(x, what, depth + 1, count, f"{at}.{k}.{n}" if at else f"{k}.{n}") for n, x in enumerate(items)]}
    extra = sorted(set(m) - LEAF)
    if extra:
        raise ValueError(f"{where}: a test is {{path, op, value}}, not {extra[0]}")
    count[0] += 1
    if count[0] > MAX_LEAVES:
        raise ValueError(f"{what} has at most {MAX_LEAVES} tests")
    problem = flow.test_problem(m)
    if problem:
        raise ValueError(f"{where}: {problem}")
    out = {"op": m["op"]}
    if m.get("path"):
        out["path"] = str(m["path"])
    if "value" in m:
        out["value"] = m["value"]
    return out


def test(tree, value, env=None):
    """Whether `value` passes the tree (checked with `check` first)."""
    if "all" in tree:
        return all(test(m, value, env) for m in tree["all"])
    if "any" in tree:
        return any(test(m, value, env) for m in tree["any"])
    if "not" in tree:
        return not test(tree["not"], value, env)
    return flow.test(tree, value)


def leaves(tree):
    """The tests in a tree, in order."""
    if "all" in tree or "any" in tree:
        return [x for m in tree.get("all", tree.get("any")) for x in leaves(m)]
    if "not" in tree:
        return leaves(tree["not"])
    return [tree]


def paths(tree):
    """The paths a tree tests, each once, in order (for checking them against a kind's fields)."""
    return list(dict.fromkeys(m.get("path") or "" for m in leaves(tree)))


def _label(path, kind):
    """A path in words (`kind` is for naming fields by their titles, once kinds have schemas)."""
    return path or "the value"


def _value(v):
    if isinstance(v, str):
        return f"“{v}”"
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "nothing"
    return str(v)


def describe(tree, kind=None):
    """The tree in plain words, as the builder and the agent show it: "the sender contains “@stmarys.org” and the
    subject is “Trip”". `kind` is the kind of activity the tree tests (events.py), for naming its fields."""
    return _describe(tree, kind, True)


def _describe(m, kind, top):
    for k, word in (("all", " and "), ("any", " or ")):
        if k in m:
            if not m[k]:
                return "always"
            parts = [_describe(x, kind, False) for x in m[k]]
            text = word.join(parts)
            return text if top or len(parts) == 1 else f"({text})"
    if "not" in m:
        inner = m["not"]
        return _leaf(inner, kind, True) if "op" in inner else f"not ({_describe(inner, kind, True)})"
    return _leaf(m, kind, False)


def _leaf(m, kind, negated):
    said = WORDS.get(m["op"], (m["op"], f"not {m['op']}"))[1 if negated else 0]
    words = f"{_label(m.get('path'), kind)} {said}"
    return words if m["op"] in ("exists", "empty") else f"{words} {_value(m.get('value'))}"
