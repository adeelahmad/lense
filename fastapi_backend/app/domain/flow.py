"""The engine every workflow runs on: graphs of nodes joined port to port, checked when saved and run in order.

A graph is `{nodes: [{id, type, config, label, x, y}], edges: [{source, target, port, input}]}`. A node has named
input ports and named output ports; an edge takes what one node passes on from an output port (`port`, default
`out`) to an input port of another (`input`, default `in`). An old edge's `branch` (a condition's yes or no) is its
port. Graphs never loop back on themselves, so every graph runs in order and ends; loops are nodes (below).

What runs, and what is skipped: a node runs once every input port it has connected has something reaching it (a
merge runs when anything reaches it). What reaches a port is what an output port passed on; a condition passes its
value on only along yes or along no, a switch only along the case that matched, so the nodes after the other ports
are skipped, and so are the ones after those.

The primitives, in every workflow (a scope adds its own nodes: workflows.py, organize.py):

- `input`: where a workflow starts (the recording, or the namespaces a routine runs over).
- `pick`: one part of the value, by a dotted path (`action_items.0.text`).
- `condition`: tests the value (or a path in it) and passes it on along `yes` or `no`.
- `switch`: tests the cases in turn and passes the value on along the first that matches (each case is a port), or
  along `default`.
- `merge`: joins what reaches it: lists into one list (entities found twice once), objects into one object.
- `set`: makes an object from fields, each a path into what came in, a value, or a template. It can take several
  inputs (`inputs`, its ports): a field's path then starts with the input's name.
- `template`: renders a template with what came in as `input` (and the recording or namespaces); `json` reads the
  result as JSON.
- `filter`: keeps the items of a list that pass a test.
- `for_each`: runs its `body` (a graph) once per item of a list and passes on the list of what the body returned;
  an item the body returned nothing for is left out (so it maps and filters). The body starts from `arg` nodes:
  `item`, `index` and `input` (all of what came in).
- `repeat`: runs its body again and again on its own result (`arg` `state`, `round`, `input`) until the result
  passes the `until` test, the body returns nothing, or `max_rounds` is reached; passes on the last result.
- `group`: a body run once: its `arg` nodes are its input ports, its `return` nodes its output ports.
- `custom`: a custom node (custom_nodes.py): a saved body, run like a group, with its parameters filled in. The
  version is pinned when the workflow is saved.
- `arg` and `return`: where a body starts and what it gives back (a return's `name` is the output port).

A run has a budget (MAX_STEPS node runs) so loops inside loops can't run away, and bodies nest at most MAX_DEPTH deep.
A dry run (`Run.dry`) keeps nothing: nodes that would save something say what they'd save.
"""

from __future__ import annotations

import copy
import json
import re
import time

from . import analyze, templates

ID_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
NAME_RX = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
PATH_RX = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")
OPS = ("exists", "empty", "equals", "not_equals", "contains", "gt", "lt")
MAX_NODES, MAX_DEPTH, MAX_STEPS = 100, 5, 20000
MAX_ITEMS, MAX_ROUNDS, MAX_CASES, MAX_FIELDS = 1000, 50, 20, 50
PRIMITIVES = (
    "input", "pick", "condition", "switch", "merge", "set", "template", "filter", "for_each", "repeat", "group", "custom",
    "arg", "return",
)  # fmt: skip
CONFIG = {
    "input": set(),
    "pick": {"path"},
    "condition": {"path", "op", "value"},
    "switch": {"path", "cases"},
    "merge": set(),
    "set": {"inputs", "fields", "keep"},
    "template": {"template", "json"},
    "filter": {"path", "op", "value"},
    "for_each": {"path", "body", "max_items"},
    "repeat": {"body", "max_rounds", "until"},
    "group": {"body"},
    "custom": {"node", "version", "params"},
    "arg": {"name"},
    "return": {"name"},
}
BODIES = ("for_each", "repeat", "group")
LOOP_ARGS = {"for_each": ("item", "index", "input"), "repeat": ("state", "round", "input")}
STARTS = ("input", "arg")


class Kit:
    """What a scope adds to the primitives: its node types and their settings, a check for those settings, how they
    run, which keep something (a top-level graph needs one), and what a top-level graph's input is called."""

    def __init__(self, scope, types, config, check, run, terminal, needs):
        self.scope, self.types, self.config, self.check, self.run = scope, tuple(types), config, check, run
        self.terminal, self.needs = set(terminal), needs

    @property
    def all_types(self):
        return PRIMITIVES + tuple(t for t in self.types if t not in PRIMITIVES)

    def settings(self, t):
        return CONFIG.get(t) if t in CONFIG else self.config[t]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---------- values ----------
def dig(value, path):
    """The part of a value at a dotted path (list items by number); None when it isn't there."""
    for part in path.split(".") if path else []:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def test(cfg, value):
    v, want, op = dig(value, cfg.get("path")), cfg.get("value"), cfg["op"]
    if op == "exists":
        return v is not None
    if op == "empty":
        return v in (None, "", [], {})
    if op == "equals":
        return v == want
    if op == "not_equals":
        return v != want
    if op == "contains":
        if isinstance(v, str):
            return str(want).lower() in v.lower()
        return isinstance(v, (list, dict)) and want in v
    if not _num(v) or not _num(want):
        return False
    return v > want if op == "gt" else v < want


def is_entity(x):
    return isinstance(x, dict) and isinstance(x.get("name"), str) and isinstance(x.get("type"), str)


def merge(values):
    """Lists joined (entities on the same line once), objects joined (later ones win), anything else listed."""
    if all(isinstance(v, list) for v in values):
        out, seen = [], set()
        for v in values:
            for x in v:
                k = (analyze.ent_key(x["name"]), x.get("seg")) if is_entity(x) else repr(x)
                if k not in seen:
                    seen.add(k)
                    out.append(x)
        return out
    if all(isinstance(v, dict) for v in values):
        out = {}
        for v in values:
            out.update(v)
        return out
    return list(values)


def substitute(value, params):
    """A body with its parameters filled in: every {"$param": name} becomes that parameter's value."""
    if isinstance(value, dict):
        if set(value) == {"$param"} and isinstance(value["$param"], str):
            return copy.deepcopy(params.get(value["$param"]))
        return {k: substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, params) for v in value]
    return value


# ---------- ports ----------
def body_ports(body):
    """(input ports, output ports) of a body: its arg names and return names, top to bottom."""

    def names(t):
        ns = sorted((n for n in body.get("nodes") or [] if n.get("type") == t), key=lambda n: (n.get("y") or 0, n.get("x") or 0))
        return list(dict.fromkeys(str((n.get("config") or {}).get("name") or "") for n in ns))

    return names("arg"), names("return")


def ports(node, kit, defs=None):
    """(input ports, output ports, input ports that take several edges) of a node."""
    t, c = node["type"], node.get("config") or {}
    if t in STARTS:
        return [], ["out"], set()
    if t == "return":
        return ["in"], [], set()
    if t == "condition":
        return ["in"], ["yes", "no"], set()
    if t == "switch":
        return ["in"], [k["port"] for k in c.get("cases") or [] if isinstance(k, dict)] + ["default"], set()
    if t == "merge":
        return ["in"], ["out"], {"in"}
    if t == "set":
        return list(c.get("inputs") or ["in"]), ["out"], set()
    if t == "group":
        ins, outs = body_ports(c.get("body") or {})
        return ins, outs, set()
    if t == "custom":
        d = (defs or {}).get((c.get("node"), c.get("version")))
        return (list(d["inputs"]), list(d["outputs"]), set()) if d else ([], [], set())
    if t in kit.terminal:
        return ["in"], [], set()
    return ["in"], ["out"], set()


def norm_edge(e, types):
    """An edge with its ports: `branch` (an old condition edge's yes or no) read as the port."""
    e = dict(e or {})
    s, d = str(e.get("source") or ""), str(e.get("target") or "")
    port = e.get("port") or e.get("branch") or ("out" if types.get(s) != "condition" else None)
    return {"source": s, "target": d, "port": port, "input": e.get("input") or "in"}


def order(nodes, edges):
    """Node ids in the order they can run (each after everything feeding it); nodes on a loop are left out."""
    ins = {n["id"]: 0 for n in nodes}
    for e in edges:
        ins[e["target"]] += 1
    ready = [n["id"] for n in nodes if ins[n["id"]] == 0]
    out = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for e in edges:
            if e["source"] == n:
                ins[e["target"]] -= 1
                if ins[e["target"]] == 0:
                    ready.append(e["target"])
    return out


# ---------- checking ----------
def check_graph(db, graph, kit, body=None, depth=0, where=""):
    """The graph, cleaned (custom nodes pinned to their current version): ValueError naming the first problem.
    `body` is what the graph is the body of (for_each, repeat, group or custom), None for a workflow."""
    if depth > MAX_DEPTH:
        raise ValueError(f"bodies nest at most {MAX_DEPTH} deep")
    graph = dict(graph or {})
    nodes, edges = graph.get("nodes") or [], graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("a workflow is {nodes: [...], edges: [...]}")
    if len(nodes) > MAX_NODES:
        raise ValueError(f"a workflow has at most {MAX_NODES} nodes{where}")
    out_nodes, by_id, defs = [], {}, {}
    for n in nodes:
        n = dict(n or {})
        nid, t, cfg = str(n.get("id") or ""), n.get("type"), dict(n.get("config") or {})
        if not ID_RX.match(nid):
            raise ValueError("node ids are 1 to 40 letters, digits, _ and -")
        if nid in by_id:
            raise ValueError(f"two nodes are called {nid}{where}")
        if t not in kit.all_types:
            raise ValueError(f"node types are {', '.join(kit.all_types)}")
        if t == "input" and body:
            raise ValueError(f"a body starts from arg nodes, not an input node{where}")
        if t in ("arg", "return") and not body:
            raise ValueError(f"{t} nodes belong in the body of a loop, group or custom node{where}")
        extra = sorted(set(cfg) - kit.settings(t))
        if extra:
            raise ValueError(f"{t} node has no setting {extra[0]}")
        here = f"{where} in {nid}" if where else f" in {nid}"
        if t in kit.types and t not in PRIMITIVES:
            kit.check(db, nid, t, cfg)
        else:
            cfg = _check_primitive(db, kit, nid, t, cfg, body, depth, here)
        if t == "custom":
            defs[(cfg["node"], cfg["version"])] = cfg.pop("_def")
        clean = {"id": nid, "type": t, "config": cfg}
        for k in ("x", "y"):
            if _num(n.get(k)):
                clean[k] = round(float(n[k]), 1)
        if isinstance(n.get("label"), str) and n["label"].strip():
            clean["label"] = n["label"].strip()[:60]
        by_id[nid] = clean
        out_nodes.append(clean)
    if not body and len([n for n in out_nodes if n["type"] == "input"]) != 1:
        raise ValueError("a workflow has exactly one input node")
    types = {n["id"]: n["type"] for n in out_nodes}
    pts = {nid: ports(n, kit, defs) for nid, n in by_id.items()}
    out_edges, taken, seen = [], {}, set()
    for raw in edges:
        e = norm_edge(raw, types)
        s, d = e["source"], e["target"]
        if s not in by_id or d not in by_id:
            raise ValueError(f"an edge joins two nodes of the workflow{where}")
        if s == d:
            raise ValueError("a node can't feed itself")
        if types[d] in STARTS:
            raise ValueError(f"nothing goes into the {types[d]} node{where}")
        if types[s] == "condition" and e["port"] not in ("yes", "no"):
            raise ValueError(f"edges from condition {s} are its yes or its no")
        if e["port"] not in pts[s][1]:
            if types[s] in kit.terminal or types[s] == "return":
                raise ValueError(f"{types[s]} node {s} passes nothing on")
            raise ValueError(f"{types[s]} node {s} has no output {e['port']} (it has {', '.join(pts[s][1]) or 'none'})")
        if e["input"] not in pts[d][0]:
            raise ValueError(f"{types[d]} node {d} has no input {e['input']} (it has {', '.join(pts[d][0])})")
        key = (s, d, e["port"], e["input"])
        if key in seen:
            continue
        seen.add(key)
        if (d, e["input"]) in taken and e["input"] not in pts[d][2]:
            if types[d] == "set" or len(pts[d][0]) > 1:
                raise ValueError(f"input {e['input']} of node {d} takes one connection")
            raise ValueError(f"node {d} takes one input; join several with a merge node")
        taken[(d, e["input"])] = True
        out_edges.append(
            {
                "source": s,
                "target": d,
                **({"port": e["port"]} if e["port"] != "out" else {}),
                **({"input": e["input"]} if e["input"] != "in" else {}),
            }
        )
    for n in out_nodes:
        need = pts[n["id"]][0]
        if n["type"] in STARTS:
            continue
        missing = [p for p in need if (n["id"], p) not in taken]
        if missing:
            if missing == need and len(need) <= 1:
                raise ValueError(f"connect something into {n['type']} node {n['id']}{where}")
            raise ValueError(f"connect input {missing[0]} of {n['type']} node {n['id']}{where}")
    if len(order(out_nodes, out_edges)) != len(out_nodes):
        raise ValueError(f"the workflow has a loop{where}; use a for each or repeat node to go over things again")
    if body in LOOP_ARGS:
        if "out" not in body_ports({"nodes": out_nodes})[1]:
            raise ValueError(f"the body{where} needs a return node called out: what it gives back")
    elif body and not body_ports({"nodes": out_nodes})[1] and not any(n["type"] in kit.terminal for n in out_nodes):
        raise ValueError(f"the body{where} needs a return node, or it gives nothing back")
    if not body and not keeps(db, out_nodes, kit):
        raise ValueError(kit.needs)
    return {"nodes": out_nodes, "edges": out_edges}


def keeps(db, nodes, kit):
    """Whether checked nodes keep something: a node that saves, here, in a body or in a custom node."""
    from . import custom_nodes

    for n in nodes:
        c = n["config"]
        if n["type"] in kit.terminal:
            return True
        if n["type"] in BODIES and keeps(db, c["body"]["nodes"], kit):
            return True
        if n["type"] == "custom" and custom_nodes.get(db, c["node"], c["version"])["keeps"]:
            return True
    return False


def check_test(nid, kind, cfg):
    if cfg.get("op") not in OPS:
        raise ValueError(f"{kind} {nid}: the test is one of {', '.join(OPS)}")
    if cfg.get("path") and not PATH_RX.match(str(cfg["path"])):
        raise ValueError(f"{kind} {nid}: give a path like summary.importance, or none")
    if cfg["op"] in ("gt", "lt") and not _num(cfg.get("value")):
        raise ValueError(f"{kind} {nid}: compare with a number")


def _whole(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _check_primitive(db, kit, nid, t, cfg, body, depth, here):
    if t == "pick":
        if not PATH_RX.match(str(cfg.get("path") or "")):
            raise ValueError(f"pick node {nid}: give a path like action_items.0.text")
    elif t in ("condition", "filter"):
        check_test(nid, t, cfg)
    elif t == "switch":
        cases = cfg.get("cases")
        if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
            raise ValueError(f"switch {nid}: give 1 to {MAX_CASES} cases")
        if cfg.get("path") and not PATH_RX.match(str(cfg["path"])):
            raise ValueError(f"switch {nid}: give a path like summary.kind, or none")
        names, clean = set(), []
        for k in cases:
            k = dict(k or {})
            if set(k) - {"port", "path", "op", "value"}:
                raise ValueError(f"switch {nid}: a case is {{port, op, value}}, and a path if it tests its own")
            if not NAME_RX.match(str(k.get("port") or "")) or k["port"] == "default" or k["port"] in names:
                raise ValueError(f"switch {nid}: name each case differently, in lowercase (not default)")
            names.add(k["port"])
            check_test(nid, "switch", k)
            clean.append(k)
        cfg["cases"] = clean
    elif t == "set":
        ins = cfg.get("inputs")
        if ins is not None:
            if not isinstance(ins, list) or not 1 <= len(ins) <= 10 or len(set(ins)) != len(ins):
                raise ValueError(f"set node {nid}: inputs are 1 to 10 different names")
            if not all(NAME_RX.match(str(x)) for x in ins):
                raise ValueError(f"set node {nid}: input names are lowercase letters, digits and _")
        fields = cfg.get("fields")
        if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS:
            raise ValueError(f"set node {nid}: give 1 to {MAX_FIELDS} fields")
        for f in fields:
            if not isinstance(f, dict) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$", str(f.get("key") or "")):
                raise ValueError(f"set node {nid}: each field has a key of letters, digits and _")
            srcs = [k for k in ("path", "value", "template") if k in f]
            if len(srcs) != 1 or set(f) - {"key", "path", "value", "template"}:
                raise ValueError(f"set node {nid}: field {f['key']} takes its value from one of a path, a value or a template")
            if "path" in f and f["path"] and not PATH_RX.match(str(f["path"])):
                raise ValueError(f"set node {nid}: field {f['key']}: give a path like summary.tldr")
            if "path" in f and ins and len(ins) > 1 and str(f["path"] or "").split(".")[0] not in ins:
                raise ValueError(f"set node {nid}: field {f['key']}: start the path with an input ({', '.join(ins)})")
            if "template" in f:
                _check_template(nid, f["template"])
    elif t == "template":
        _check_template(nid, cfg.get("template"))
    elif t == "for_each":
        if cfg.get("path") and not PATH_RX.match(str(cfg["path"])):
            raise ValueError(f"for each {nid}: give a path to the list, like action_items, or none")
        if cfg.get("max_items") is not None and not _whole(cfg["max_items"], 1, MAX_ITEMS):
            raise ValueError(f"for each {nid}: at most is 1 to {MAX_ITEMS} items")
    elif t == "repeat":
        if cfg.get("max_rounds") is not None and not _whole(cfg["max_rounds"], 1, MAX_ROUNDS):
            raise ValueError(f"repeat {nid}: at most is 1 to {MAX_ROUNDS} rounds")
        if cfg.get("until") is not None:
            if not isinstance(cfg["until"], dict):
                raise ValueError(f"repeat {nid}: until is a test {{path, op, value}}")
            check_test(nid, "repeat", cfg["until"])
    elif t == "custom":
        cfg = _check_custom(db, kit, nid, cfg, depth, here)
    elif t in ("arg", "return"):
        if not NAME_RX.match(str(cfg.get("name") or "")):
            raise ValueError(f"{t} node {nid}: name it with lowercase letters, digits and _")
        if t == "arg" and body in LOOP_ARGS and cfg["name"] not in LOOP_ARGS[body]:
            raise ValueError(f"arg node {nid}: a {body.replace('_', ' ')} body has {', '.join(LOOP_ARGS[body])}")
        if t == "return" and body in LOOP_ARGS and cfg["name"] != "out":
            raise ValueError(f"return node {nid}: a {body.replace('_', ' ')} body returns out")
    if t in BODIES:
        if not isinstance(cfg.get("body"), dict):
            raise ValueError(f"{t} node {nid}: it needs a body")
        cfg["body"] = check_graph(db, cfg["body"], kit, t, depth + 1, here)
        if t == "group":
            ins, outs = body_ports(cfg["body"])
            if not ins:
                raise ValueError(f"group {nid}: its body needs an arg node, for what comes in")
            if len(outs) > 10 or len(ins) > 10:
                raise ValueError(f"group {nid}: at most 10 inputs and 10 outputs")
    return cfg


def _check_template(nid, body):
    if not isinstance(body, str) or not body.strip() or len(body) > 20000:
        raise ValueError(f"template node {nid}: write a template (up to 20000 characters)")
    try:
        templates.check(body)
    except templates.TemplateProblem as e:
        raise ValueError(f"template node {nid}: {e}") from None


def _check_custom(db, kit, nid, cfg, depth, here):
    from . import custom_nodes

    try:
        d = custom_nodes.get(db, int(cfg.get("node") or 0), cfg.get("version"))
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"custom node {nid}: choose a custom node") from None
    if d.get("deleted_at") and not cfg.get("version"):
        raise ValueError(f"custom node {nid}: {d['name']} was removed")
    if kit.scope not in d["scopes"]:
        raise ValueError(f"custom node {nid}: {d['name']} is for {' and '.join(d['scopes'])} workflows")
    params = cfg.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"custom node {nid}: params are {{name: value}}")
    known = {p["name"]: p for p in d["params"]}
    for k, v in params.items():
        if k not in known:
            raise ValueError(f"custom node {nid}: {d['name']} has no parameter {k}")
        custom_nodes.check_value(known[k], v, f"custom node {nid}")
    filled = {**{p["name"]: p.get("default") for p in d["params"]}, **params}
    check_graph(db, substitute(d["graph"], filled), kit, "custom", depth + 1, here)
    out = {"node": d["id"], "version": d["version"], **({"params": params} if params else {})}
    out["_def"] = {"inputs": d["inputs"], "outputs": d["outputs"]}
    return out


# ---------- running ----------
class Run:
    """One run of a workflow: what its nodes can use (db, cfg, the start value `ctx`), where its log goes, whether it
    keeps anything (`dry`), and what each node did (`trace`: {node path: {status, ms, value, ports}})."""

    def __init__(self, db, cfg, kit, ctx, say=print, dry=False, user=None, origin=None, **extra):
        self.db, self.cfg, self.kit, self.ctx, self.say, self.dry = db, cfg, kit, ctx, say, dry
        self.user, self.origin, self.extra = user, origin or {}, extra
        self.steps, self.trace, self.totals, self._defs = 0, {}, {}, {}

    def tick(self):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise ValueError(f"the workflow ran {MAX_STEPS} nodes and was stopped; check its loops")

    def custom(self, node, version):
        from . import custom_nodes

        if (node, version) not in self._defs:
            self._defs[(node, version)] = custom_nodes.get(self.db, node, version)
        return self._defs[(node, version)]


class Frame:
    """One graph being run: its nodes, what each passed on, and the llm nodes' templates and models (`made`)."""

    def __init__(self, graph, prefix):
        self.graph, self.prefix, self.values, self.made = graph, prefix, {}, {}
        self.by_id = {n["id"]: n for n in graph["nodes"]}

    def upstream(self, nid, found):
        """The nearest node above `nid` that `found` has, by breadth: its entry, or {}."""
        seen, todo = set(), [nid]
        while todo:
            n = todo.pop(0)
            for e in self.graph["edges"]:
                if e["target"] == n and e["source"] not in seen:
                    if e["source"] in found:
                        return found[e["source"]]
                    seen.add(e["source"])
                    todo.append(e["source"])
        return {}


def preview(v, limit=4000):
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = repr(v)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def run_graph(run, graph, args=None, prefix="", trace=True):
    """Run a graph; returns what its return nodes gave back ({name: value}). `run.trace` hears what each node did."""
    f = Frame(graph, prefix)
    types = {n["id"]: n["type"] for n in graph["nodes"]}
    edges = [norm_edge(e, types) for e in graph["edges"]]
    returned = {}
    for nid in order(graph["nodes"], edges):
        node = f.by_id[nid]
        run.tick()
        inputs, connected, live = {}, set(), set()
        for e in edges:
            if e["target"] != nid:
                continue
            connected.add(e["input"])
            src = f.values.get(e["source"])
            if src is not None and e["port"] in src:
                live.add(e["input"])
                inputs.setdefault(e["input"], []).append(src[e["port"]])
        t = node["type"]
        ok = t in STARTS or (bool(live) if t == "merge" else bool(connected) and live == connected)
        path = prefix + nid
        if not ok:
            if trace:
                run.trace[path] = {"status": "skipped"}
            continue
        many = ports(node, run.kit, {})[2]
        vals = {p: (merge(vs) if p in many else vs[0]) for p, vs in inputs.items()}
        t0 = time.monotonic()
        try:
            out = _step(run, f, node, vals, args or {}, returned, trace)
        except Exception as e:
            if trace:
                run.trace[path] = {"status": "failed", "error": str(e)[:500], "ms": round((time.monotonic() - t0) * 1000)}
            raise
        f.values[nid] = out
        if trace:
            shown = next(iter(out.values())) if out else vals.get("in", next(iter(vals.values()), None))
            run.trace[path] = {
                "status": "done",
                "ms": round((time.monotonic() - t0) * 1000),
                "ports": list(out),
                "value": preview(shown),
            }
    return returned


def _label(node):
    return node.get("label") or f"{node['type']} {node['id']}"


def _step(run, f, node, vals, args, returned, trace):
    t, c, name = node["type"], node["config"], _label(node)
    v = vals.get("in")
    path = f.prefix + node["id"] + "/"
    if t == "input":
        return {"out": run.ctx}
    if t == "arg":
        return {"out": args.get(c["name"])}
    if t == "return":
        returned[c["name"]] = v
        return {}
    if t == "pick":
        return {"out": dig(v, c["path"])}
    if t == "condition":
        yes = test(c, v)
        run.say(f"{name}: {'yes' if yes else 'no'}")
        return {"yes" if yes else "no": v}
    if t == "switch":
        for k in c["cases"]:
            if test({**k, "path": k.get("path") or c.get("path")}, v):
                run.say(f"{name}: {k['port']}")
                return {k["port"]: v}
        run.say(f"{name}: default")
        return {"default": v}
    if t == "merge":
        return {"out": v}
    if t == "set":
        return {"out": _set(run, c, vals)}
    if t == "template":
        text = templates.render_body(c["template"], {**_tctx(run), "input": None if v is run.ctx else v}, "prompt")
        if c.get("json"):
            try:
                return {"out": json.loads(text)}
            except ValueError:
                raise ValueError(f"{name}: what it rendered isn't JSON") from None
        return {"out": text}
    if t == "filter":
        kept = [x for x in v if test(c, x)] if isinstance(v, list) else []
        run.say(f"{name}: kept {len(kept)}")
        return {"out": kept}
    if t == "for_each":
        items = dig(v, c["path"]) if c.get("path") else v
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ValueError(f"{name}: needs a list, got {type(items).__name__}")
        cap = c.get("max_items") or 200
        if len(items) > cap:
            run.say(f"{name}: {len(items)} items, going over the first {cap}")
        out = []
        for k, item in enumerate(items[:cap]):
            r = run_graph(run, c["body"], {"item": item, "index": k, "input": v}, path, trace and k == 0)
            if "out" in r:
                out.append(r["out"])
        run.say(f"{name}: {len(out)} of {min(len(items), cap)} items")
        return {"out": out}
    if t == "repeat":
        state, rounds = v, 0
        for k in range(c.get("max_rounds") or 5):
            r = run_graph(run, c["body"], {"state": state, "round": k, "input": v}, path, trace and k == 0)
            if "out" not in r:
                break
            state, rounds = r["out"], k + 1
            if c.get("until") and test(c["until"], state):
                break
        run.say(f"{name}: {rounds} rounds")
        return {"out": state}
    if t == "group":
        return run_graph(run, c["body"], vals, path, trace)
    if t == "custom":
        d = run.custom(c["node"], c["version"])
        params = {**{p["name"]: p.get("default") for p in d["params"]}, **(c.get("params") or {})}
        return run_graph(run, substitute(d["graph"], params), vals, path, trace)
    return run.kit.run(run, f, node, v, vals)


def _tctx(run):
    return run.ctx if isinstance(run.ctx, dict) else {}


def _set(run, c, vals):
    ins = c.get("inputs") or ["in"]
    single = len(ins) == 1
    base = vals.get(ins[0]) if single else {k: vals.get(k) for k in ins}
    out = dict(base) if c.get("keep") and isinstance(base, dict) and base is not run.ctx else {}
    for fdef in c["fields"]:
        if "value" in fdef:
            out[fdef["key"]] = copy.deepcopy(fdef["value"])
        elif "path" in fdef:
            out[fdef["key"]] = dig(base, fdef["path"]) if fdef["path"] else base
        else:
            inp = base if not single else (None if base is run.ctx else base)
            out[fdef["key"]] = templates.render_body(fdef["template"], {**_tctx(run), "input": inp}, "prompt")
    return out
