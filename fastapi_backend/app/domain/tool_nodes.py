"""Tool graphs: an assistant tool drawn on the canvas (extensions.py, a tool whose body is `graph`).

The graph is a body like a custom node's: it starts from `arg` nodes, one per parameter of the tool, and what reaches
its `return` nodes is what the tool gives back (just the value when there's one return node called `out`). Between
them are the primitives (pick, condition, switch, set, template, filter, loops, groups, custom nodes made of these)
and two nodes of its own:

- `ask_model`: writes a prompt (a template, with the value coming in as `input`) and passes on the model's reply, as
  JSON when `json` is set.
- `call_tool`: calls one of the assistant's tools (its own, or another extension's tool), with the value coming in
  (an object) laid over the arguments set on the node, and passes on what the tool gave back. Tools that change
  something still ask the person first.
"""

from __future__ import annotations

import json

from . import flow, llm, templates

OWN_NODES = ("ask_model", "call_tool")
CONFIG = {"ask_model": {"prompt", "system", "json", "model"}, "call_tool": {"tool", "args"}}
MAX_DEPTH = 3  # tool graphs calling tool graphs


def check_config(db, nid, t, cfg):
    if t == "ask_model":
        if not isinstance(cfg.get("prompt"), str) or not cfg["prompt"].strip():
            raise ValueError(f"ask model node {nid}: write its prompt")
        for k in ("system", "model"):
            if cfg.get(k) is not None and not isinstance(cfg[k], str):
                raise ValueError(f"ask model node {nid}: {k} is text")
        try:
            templates.render_body(cfg["prompt"], {"input": None}, "prompt")
        except templates.TemplateProblem as e:
            if "line" in str(e) or "not allowed" in str(e):
                raise ValueError(f"ask model node {nid}: {e}") from None
    elif t == "call_tool":
        from . import extensions

        if not extensions.NAME_RX.match(str(cfg.get("tool") or "")) and cfg.get("tool") != "use_skill":
            raise ValueError(f"call tool node {nid}: name the tool it calls")
        if cfg.get("args") is not None and not isinstance(cfg["args"], dict):
            raise ValueError(f"call tool node {nid}: its arguments are an object")


def run_node(r, f, node, value, vals):
    t, c, name = node["type"], node["config"], flow._label(node)
    if t == "ask_model":
        prompt = templates.render_body(c["prompt"], {"input": value}, "prompt")
        msgs = ([{"role": "system", "content": c["system"]}] if c.get("system") else []) + [{"role": "user", "content": prompt}]
        text = llm.chat(r.cfg, msgs, model=c.get("model"))
        r.say(f"{name}: asked the model")
        if c.get("json"):
            try:
                return {"out": llm.parse_json(text)}
            except (ValueError, llm.LLMError):
                raise ValueError(f"{name}: the model's reply isn't JSON") from None
        return {"out": text}
    box = r.extra.get("toolbox")
    if box is None:
        raise ValueError(f"{name}: tools can only be called from a conversation or a tool's try")
    args = {**(c.get("args") or {}), **(value if isinstance(value, dict) else {})}
    box.depth = getattr(box, "depth", 0) + 1
    try:
        if box.depth > MAX_DEPTH:
            raise ValueError(f"{name}: tools call each other more than {MAX_DEPTH} deep")
        result, summary = box.call(c["tool"], args)
    finally:
        box.depth -= 1
    r.say(f"{name}: {summary}")
    out = json.loads(result)
    if isinstance(out, dict) and set(out) == {"error"}:
        raise ValueError(f"{name}: {out['error']}")
    return {"out": out}


def check(db, graph, params):
    """A tool's graph, cleaned, checked against its parameters: ValueError naming the first problem."""
    from . import workflows

    clean = flow.check_graph(db, graph, workflows.KITS["tool"], "custom")
    ins, outs = flow.body_ports(clean)
    names = {p["name"] for p in params}
    extra = [a for a in ins if a not in names]
    if extra:
        raise ValueError(f"the graph's arg node {extra[0]} isn't one of the tool's parameters")
    if not outs:
        raise ValueError("the graph needs a return node: what the tool gives back")
    return clean


def run(db, cfg, graph, args, toolbox=None):
    """Run a tool's graph with checked arguments: what its return nodes gave back."""
    from . import workflows

    log = []
    r = flow.Run(db, cfg, workflows.KITS["tool"], args, log.append, toolbox=toolbox)
    got = flow.run_graph(r, graph, args, trace=False)
    return got["out"] if set(got) == {"out"} else got
