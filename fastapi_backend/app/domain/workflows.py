"""Workflows: named, versioned graphs of nodes, drawn on a canvas, that turn what a pipeline made of a recording into
metadata. A pipeline's steps make assets (a transcript, shots, text on screen, faces); the workflows it attaches run
after them and make named outputs, custom field values and entities.

A graph is `{nodes: [{id, type, config, label, x, y}], edges: [{source, target, branch}]}`. Exactly one `input` node
starts it with the recording (what templates see: transcript, summary, entities, outputs...). Every other node takes
the value of the node feeding it (a merge node takes several):

- `llm`: renders a prompt template (with the value as `input`) and passes on the model's JSON reply.
- `pick`: passes on one part of the value, by a dotted path (`action_items.0.text`).
- `condition`: tests the value (or a path in it) and passes it on along its `yes` or its `no` edges.
- `merge`: joins what reaches it: lists into one list (entities found twice once), objects into one object.
- `extract_rules`: finds entities in the transcript with rules: the built-in extractor (as analyze uses), your terms
  ("Name|TYPE") and your regular expressions. Passes on a list of entities, each with the line it was found on.
- `extract_llm`: asks the model for the entities in the transcript, of the types you name; entities passed in are given
  to it as what was found so far, to keep, correct or add to.
- `output`: saves the value as a named output of the recording.
- `field`: sets a custom field of the recording to the value, checked like an edit (and kept in its history).
- `save_entities`: makes the entities passed in the recording's entities (in place of what analyze found), with
  people's corrections kept, then redoes keywords and sections as analyze does.

Nodes on a branch a condition didn't take are skipped (a merge runs when anything reaches it). Positions are only for
the canvas.
"""

from __future__ import annotations

import re

from . import analyze, fields as fieldmod, llm, metadata, pipelines, store, templates

R = store.R
NODE_TYPES = ("input", "llm", "pick", "condition", "merge", "extract_rules", "extract_llm", "output", "field", "save_entities")
TERMINAL = {"output", "field", "save_entities"}
OPS = ("exists", "empty", "equals", "not_equals", "contains", "gt", "lt")
CONFIG = {
    "input": set(),
    "llm": {"template", "version", "model"},
    "pick": {"path"},
    "condition": {"path", "op", "value"},
    "merge": set(),
    "extract_rules": {"builtin", "terms", "patterns", "types"},
    "extract_llm": {"types", "instructions", "model"},
    "output": {"key"},
    "field": {"field"},
    "save_entities": set(),
}
ENTITY_TYPES = ("PERSON", "ORG", "PRODUCT", "PLACE", "EVENT", "WORK", "TERM", "DATE", "NUMBER")
ID_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
PATH_RX = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")
TYPE_RX = re.compile(r"^[A-Z][A-Z_]{0,30}$")
MAX_NODES, MAX_RULES = 100, 500
ENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "type": {"type": "string"}, "line": {"type": "integer"}},
                "required": ["name", "type"],
            },
        }
    },
    "required": ["entities"],
}
ENTITY_SYSTEM = (
    "You find named things in a transcript: people, organisations, products, places, events, works and topics. Use the "
    "names as they are written in the transcript. Give each one's type and the number of a line it is on. Reply with "
    "JSON only."
)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_graph(db, graph):
    """The graph, cleaned: ValueError naming the first problem."""
    graph = dict(graph or {})
    nodes, edges = graph.get("nodes") or [], graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("a workflow is {nodes: [...], edges: [...]}")
    if len(nodes) > MAX_NODES:
        raise ValueError(f"a workflow has at most {MAX_NODES} nodes")
    out_nodes, by_id = [], {}
    for n in nodes:
        n = dict(n or {})
        nid, t, cfg = str(n.get("id") or ""), n.get("type"), dict(n.get("config") or {})
        if not ID_RX.match(nid):
            raise ValueError("node ids are 1 to 40 letters, digits, _ and -")
        if nid in by_id:
            raise ValueError(f"two nodes are called {nid}")
        if t not in NODE_TYPES:
            raise ValueError(f"node types are {', '.join(NODE_TYPES)}")
        extra = sorted(set(cfg) - CONFIG[t])
        if extra:
            raise ValueError(f"{t} node has no setting {extra[0]}")
        _check_config(db, nid, t, cfg)
        clean = {"id": nid, "type": t, "config": cfg}
        for k in ("x", "y"):
            if _num(n.get(k)):
                clean[k] = round(float(n[k]), 1)
        if isinstance(n.get("label"), str) and n["label"].strip():
            clean["label"] = n["label"].strip()[:60]
        by_id[nid] = clean
        out_nodes.append(clean)
    starts = [n for n in out_nodes if n["type"] == "input"]
    if len(starts) != 1:
        raise ValueError("a workflow has exactly one input node")
    out_edges, incoming, seen = [], {}, set()
    for e in edges:
        e = dict(e or {})
        s, d, b = str(e.get("source") or ""), str(e.get("target") or ""), e.get("branch")
        if s not in by_id or d not in by_id:
            raise ValueError("an edge joins two nodes of the workflow")
        if s == d:
            raise ValueError("a node can't feed itself")
        if by_id[d]["type"] == "input":
            raise ValueError("nothing goes into the input node")
        if by_id[s]["type"] == "condition":
            if b not in ("yes", "no"):
                raise ValueError(f"edges from condition {s} are its yes or its no")
        elif b is not None:
            raise ValueError(f"only a condition's edges have a branch ({s} → {d})")
        if (s, d, b) in seen:
            continue
        seen.add((s, d, b))
        if d in incoming and by_id[d]["type"] != "merge":
            raise ValueError(f"node {d} takes one input; join several with a merge node")
        incoming.setdefault(d, []).append(s)
        out_edges.append(store.clean({"source": s, "target": d, "branch": b}))
    for n in out_nodes:
        if n["type"] != "input" and n["id"] not in incoming:
            raise ValueError(f"connect something into {n['type']} node {n['id']}")
    if len(order(out_nodes, out_edges)) != len(out_nodes):
        raise ValueError("the workflow has a loop")
    if not any(n["type"] in TERMINAL for n in out_nodes):
        raise ValueError("a workflow needs an output, field or save entities node, or it keeps nothing")
    return {"nodes": out_nodes, "edges": out_edges}


def _check_config(db, nid, t, cfg):
    if t == "llm":
        try:
            tpl = templates.get(db, int(cfg.get("template") or 0), cfg.get("version"))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"llm node {nid}: choose a prompt template") from None
        if tpl["kind"] != "prompt":
            raise ValueError(f"llm node {nid} needs a prompt template, not {tpl['kind']}")
    elif t == "pick":
        if not PATH_RX.match(str(cfg.get("path") or "")):
            raise ValueError(f"pick node {nid}: give a path like action_items.0.text")
    elif t == "condition":
        if cfg.get("op") not in OPS:
            raise ValueError(f"condition {nid}: the test is one of {', '.join(OPS)}")
        if cfg.get("path") and not PATH_RX.match(str(cfg["path"])):
            raise ValueError(f"condition {nid}: give a path like summary.importance, or none")
        if cfg["op"] in ("gt", "lt") and not _num(cfg.get("value")):
            raise ValueError(f"condition {nid}: compare with a number")
    elif t == "extract_rules":
        terms, pats = cfg.get("terms") or [], cfg.get("patterns") or []
        if not isinstance(terms, list) or not all(isinstance(x, str) for x in terms):
            raise ValueError(f"extract node {nid}: terms are lines like Acme Corp|ORG")
        if not isinstance(pats, list) or len(terms) + len(pats) > MAX_RULES:
            raise ValueError(f"extract node {nid}: patterns are [{{pattern, type}}], {MAX_RULES} rules at most")
        for p in pats:
            if not isinstance(p, dict) or not TYPE_RX.match(str(p.get("type") or "")):
                raise ValueError(f"extract node {nid}: each pattern has a type in capitals, e.g. TICKET")
            try:
                re.compile(str(p.get("pattern") or ""))
            except re.error as e:
                raise ValueError(f"extract node {nid}: pattern {p.get('pattern')!r} doesn't work ({e})") from None
            if not p.get("pattern"):
                raise ValueError(f"extract node {nid}: a pattern is empty")
        _check_types(nid, cfg)
    elif t == "extract_llm":
        _check_types(nid, cfg)
        if cfg.get("instructions") is not None and not isinstance(cfg["instructions"], str):
            raise ValueError(f"extract node {nid}: instructions are text")
    elif t == "output":
        if not pipelines.KEY_RX.match(str(cfg.get("key") or "")):
            raise ValueError(f"output node {nid}: name it with lowercase letters, digits and _ (e.g. meeting_notes)")
    elif t == "field":
        try:
            fieldmod.get(db, int(cfg.get("field") or 0))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"field node {nid}: choose a custom field") from None


def _check_types(nid, cfg):
    types = cfg.get("types")
    if types is not None and (not isinstance(types, list) or not all(TYPE_RX.match(str(x)) for x in types)):
        raise ValueError(f"extract node {nid}: types are names in capitals, e.g. PERSON, ORG")


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


def create(db, name, graph, description=None, user=None):
    if not (name or "").strip():
        raise ValueError("give the workflow a name")
    graph = validate_graph(db, graph)
    wid, t = db.next_id("workflow"), store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("workflow", wid),
        d=store.clean(
            {"name": name.strip()[:80], "description": description, "current": 1, "created_at": t, "updated_at": t, "created_by": user}
        ),
    )
    db.q(
        "CREATE $r CONTENT $d",
        r=R("workflow_version", f"{wid}-1"),
        d=store.clean({"workflow": wid, "version": 1, "graph": graph, "created_at": t, "created_by": user}),
    )
    return wid


def save_version(db, wid, graph, notes=None, user=None, publish=True):
    if not db.one("SELECT id FROM $r", r=R("workflow", wid)):
        raise KeyError(wid)
    graph = validate_graph(db, graph)
    n = max(db.values("SELECT VALUE version FROM workflow_version WHERE workflow = $w", w=wid) or [0]) + 1
    db.q(
        "CREATE $r CONTENT $d",
        r=R("workflow_version", f"{wid}-{n}"),
        d=store.clean({"workflow": wid, "version": n, "graph": graph, "notes": notes, "created_at": store.now(), "created_by": user}),
    )
    db.q("UPDATE $r SET updated_at = $t" + (", current = $n" if publish else ""), r=R("workflow", wid), t=store.now(), n=n)
    return n


def rename(db, wid, name=None, description=None):
    if not db.one("SELECT id FROM $r", r=R("workflow", wid)):
        raise KeyError(wid)
    if name is not None:
        if not name.strip():
            raise ValueError("give the workflow a name")
        db.q("UPDATE $r SET name = $n", r=R("workflow", wid), n=name.strip()[:80])
    if description is not None:
        db.q("UPDATE $r SET description = $d", r=R("workflow", wid), d=description.strip() or None)


def get(db, wid, version=None):
    w = db.one("SELECT record::id(id) AS id, name, description, current, created_at, updated_at FROM $r", r=R("workflow", wid))
    if not w:
        raise KeyError(wid)
    v = db.one("SELECT version, graph, notes, created_at, created_by FROM $r", r=R("workflow_version", f"{wid}-{version or w['current']}"))
    if not v:
        raise KeyError(f"{wid} v{version}")
    return {**w, **v}


def history(db, wid):
    return db.rows("SELECT version, notes, created_at, created_by FROM workflow_version WHERE workflow = $w ORDER BY version DESC", w=wid)


def list_workflows(db):
    used = {}
    for p in db.rows("SELECT record::id(id) AS id, name, current FROM pipeline"):
        v = db.one("SELECT steps FROM $r", r=R("pipeline_version", f"{p['id']}-{p['current']}")) or {}
        for s in v.get("steps") or []:
            if isinstance(s, dict) and s.get("type") == "workflow" and p["name"] not in used.get(s.get("workflow"), []):
                used.setdefault(s.get("workflow"), []).append(p["name"])
    return [
        {**w, "pipelines": used.get(w["id"], [])}
        for w in db.rows("SELECT record::id(id) AS id, name, description, current, updated_at FROM workflow ORDER BY id")
    ]


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


def merge(values):
    """Lists joined (entities on the same line once), objects joined (later ones win), anything else listed."""
    if all(isinstance(v, list) for v in values):
        out, seen = [], set()
        for v in values:
            for x in v:
                k = (analyze.ent_key(x["name"]), x.get("seg")) if _is_entity(x) else repr(x)
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


def _is_entity(x):
    return isinstance(x, dict) and isinstance(x.get("name"), str) and isinstance(x.get("type"), str)


def _segments(db, rid):
    return db.rows("SELECT idx, speaker, text FROM segment WHERE recording = $r ORDER BY idx", r=rid)


def extract_rules(db, cfg, rid, c):
    """Entities found by rules, line by line: [{name, type, seg}] (seg: the line's place in the transcript)."""
    gaz = analyze.parse_gazetteer(
        list(c.get("terms") or []) + (list(cfg["analysis"].get("gazetteer") or []) if c.get("builtin", True) else [])
    )
    pats = [(re.compile(p["pattern"]), p["type"]) for p in c.get("patterns") or []]
    spacy = c.get("builtin", True) and cfg["analysis"]["entities"] == "spacy"
    types = set(c.get("types") or [])
    out = []
    for k, s in enumerate(_segments(db, rid)):
        text, found = s["text"] or "", []
        for rx, typ in pats:
            found += [(m.group(0), typ) for m in rx.finditer(text) if m.group(0).strip()]
        if c.get("builtin", True) and not spacy:
            found += analyze.extract_entities(text, gaz)  # as analyze finds them, your terms included
        else:
            found += [(m.group(0), typ) for _, typ, rx in gaz for m in rx.finditer(text)]
            if spacy:
                found += analyze.spacy_entities(text, cfg["analysis"]["spacy_model"])
        out += [{"name": n.strip(), "type": t, "seg": k} for n, t in found if not types or t in types]
    return merge([out])


def extract_llm(db, cfg, rid, c, so_far):
    """Entities the model finds, with the line each is on; `so_far` (entities from earlier nodes) is shown to it."""
    segs = _segments(db, rid)
    cap = cfg["llm"].get("max_chars") or 24000
    lines = "\n".join(f"{k}: {s['text']}" for k, s in enumerate(segs))[:cap]
    types = c.get("types") or [t for t in ENTITY_TYPES if t not in ("DATE", "NUMBER")]
    ask = [f"Types: {', '.join(types)}."]
    if c.get("instructions"):
        ask.append(c["instructions"].strip())
    if so_far:
        ask.append(
            "Found so far (keep the right ones, correct their names or types, add what is missing):\n"
            + "\n".join(f"- {e['name']} ({e['type']})" + (f", line {e['seg']}" if e.get("seg") is not None else "") for e in so_far)
        )
    ask.append("Transcript, one numbered line each:\n" + lines)
    reply = llm.json_out(cfg, ENTITY_SYSTEM, "\n\n".join(ask), ENTITY_SCHEMA, c.get("model"))
    out = []
    for e in (reply or {}).get("entities") or []:
        if not _is_entity(e) or not e["name"].strip():
            continue
        typ = re.sub(r"[^A-Z_]", "", e["type"].upper()) or "TERM"
        line = e.get("line")
        seg = line if isinstance(line, int) and 0 <= line < len(segs) else None
        if types and typ not in types:
            continue
        out.append({"name": e["name"].strip()[:120], "type": typ, "seg": seg})
    return merge([out])


def save_entities(db, cfg, rid, ents):
    """The recording's entities become these: each on its line, or on every line that names it when no line is known."""
    segs = _segments(db, rid)
    per = [[] for _ in segs]
    for e in ents or []:
        if not _is_entity(e):
            continue
        if isinstance(e.get("seg"), int) and 0 <= e["seg"] < len(segs):
            per[e["seg"]].append((e["name"], e["type"]))
            continue
        rx = re.compile(rf"(?<!\w){re.escape(e['name'])}(?!\w)", re.I)
        for k, s in enumerate(segs):
            if rx.search(s["text"] or ""):
                per[k].append((e["name"], e["type"]))
    analyze.analyze_recording(db, cfg, rid, seg_ents=per)
    return sum(len(x) for x in per)


def run(db, cfg, rid, wid, version=None, say=print, user=None):
    """Run one version (default: the current one) of a workflow on a recording. Returns what each node did:
    {node id: done | skipped}."""
    w = get(db, wid, version)
    graph = w["graph"]
    by_id = {n["id"]: n for n in graph["nodes"]}
    ctx = templates.context(db, cfg, rid)
    origin = {"workflow": w["id"], "workflow_version": w["version"]}
    values, outcome = {}, {}
    passed, made = {}, {}  # a condition's answer; an llm node's template and model
    for nid in order(graph["nodes"], graph["edges"]):
        node = by_id[nid]
        name, c = node.get("label") or f"{node['type']} {nid}", node["config"]
        if node["type"] == "input":
            values[nid], outcome[nid] = ctx, "done"
            continue
        live = [
            e["source"]
            for e in graph["edges"]
            if e["target"] == nid
            and outcome.get(e["source"]) == "done"
            and (by_id[e["source"]]["type"] != "condition" or passed.get(e["source"]) == (e.get("branch") == "yes"))
        ]
        if not live:
            outcome[nid] = "skipped"
            continue
        value = values[live[0]]
        upstream = None if value is ctx else value
        if node["type"] == "llm":
            t = templates.get(db, int(c["template"]), c.get("version"))
            prompt = templates.render_body(t["body"], {**ctx, "input": upstream}, "prompt")
            values[nid] = llm.json_out(
                cfg, t.get("system") or templates.DEFAULT_SYSTEM, prompt, t.get("schema") or {"type": "object"}, c.get("model")
            )
            made[nid] = {"template": t["id"], "version": t["version"], "model": c.get("model") or cfg["llm"]["model"]}
            say(f"{name}: asked {made[nid]['model']}")
        elif node["type"] == "pick":
            values[nid] = dig(value, c["path"])
        elif node["type"] == "condition":
            values[nid], passed[nid] = value, test(c, value)
            say(f"{name}: {'yes' if passed[nid] else 'no'}")
        elif node["type"] == "merge":
            values[nid] = merge([values[s] for s in live])
        elif node["type"] == "extract_rules":
            values[nid] = extract_rules(db, cfg, rid, c)
            say(f"{name}: {len(values[nid])} found")
        elif node["type"] == "extract_llm":
            so_far = [e for e in upstream if _is_entity(e)] if isinstance(upstream, list) else []
            values[nid] = extract_llm(db, cfg, rid, c, so_far)
            say(f"{name}: {len(values[nid])} found")
        elif node["type"] == "output":
            pipelines.save_output(db, rid, c["key"], value, {**origin, **_made_by(made, graph, nid)})
            say(f"saved output {c['key']}")
        elif node["type"] == "field":
            f = fieldmod.get(db, int(c["field"]))
            defs, row = fieldmod.resource_fields(db, rid)
            try:
                after = fieldmod.merged(row, defs, {f["id"]: _fit(f, value)})
            except ValueError as e:
                raise ValueError(f"{name}: {e}") from None
            if after != (row.get("fields") or {}):
                metadata.save_fields(db, cfg, rid, after, user or f"workflow:{w['id']}")
            say(f"set {f['label']}")
        elif node["type"] == "save_entities":
            if not isinstance(value, list):
                raise ValueError(f"{name}: needs a list of entities")
            say(f"{name}: {save_entities(db, cfg, rid, value)} mentions kept")
        outcome[nid] = "done"
    return {n["id"]: outcome.get(n["id"], "skipped") for n in graph["nodes"]}


def _made_by(made, graph, nid):
    """The template and model of the nearest llm node above a node, if any."""
    seen, todo = set(), [nid]
    while todo:
        n = todo.pop(0)
        for e in graph["edges"]:
            if e["target"] == n and e["source"] not in seen:
                if e["source"] in made:
                    return made[e["source"]]
                seen.add(e["source"])
                todo.append(e["source"])
    return {}


def _fit(f, v):
    """A model's value shaped for a field: lists joined for text, one item for a single choice."""
    if isinstance(v, dict):
        v = next(iter(v.values()), None) if len(v) == 1 else None
    if isinstance(v, list) and all(_is_entity(x) for x in v) and v:
        v = list(dict.fromkeys(x["name"] for x in v))
    if f["type"] in ("text", "longtext", "link") and isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if f["type"] == "choice" and isinstance(v, list):
        return v[0] if v else None
    if f["type"] == "choices" and isinstance(v, str):
        return [v]
    return v
