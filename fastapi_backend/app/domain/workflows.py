"""Workflows: named, versioned graphs of nodes, drawn on a canvas, that turn what a pipeline made of a recording into
metadata. A pipeline's steps make assets (a transcript, shots, text on screen, faces); the workflows it attaches run
after them and make named outputs, custom field values and entities.

A graph is `{nodes: [{id, type, config, label, x, y}], edges: [{source, target, port, input}]}`, run by flow.py: one
`input` node starts it with the recording (what templates see: transcript, summary, entities, outputs...), and the
primitives there (pick, condition, switch, merge, set, template, filter, for each, repeat, group, custom nodes) shape
what flows between these, the nodes of a recording workflow:

- `llm`: renders a prompt template (with the value as `input`) and passes on the model's JSON reply.
- `extract_rules`: finds entities in the transcript with rules: the built-in extractor (as analyze uses), your terms
  ("Name|TYPE") and your regular expressions. Passes on a list of entities, each with the line it was found on.
- `extract_llm`: asks the model for the entities in the transcript, of the types you name; entities passed in are given
  to it as what was found so far, to keep, correct or add to.
- `output`: saves the value as a named output of the recording.
- `field`: sets a custom field of the recording to the value, checked like an edit (and kept in its history).
- `save_entities`: makes the entities passed in the recording's entities (in place of what analyze found), with
  people's corrections kept, then redoes keywords and sections as analyze does.
- `classify`: which of some labels (or the content types, or the namespace's topics) fits the asset, or what came
  in as text, as classify.py says: passes on {choice, confidence, sure, ranked, by}. With `apply` it acts on a sure
  answer (sets the content type, suggests the topic, adds the tag), so it keeps something.

Positions are only for the canvas.

A workflow's scope says what it runs on: `recording` (the nodes above, run by pipelines), `graph` (run over
namespaces, usually by a routine, to organise their entities: see organize.py for its nodes) or `event` (what an
automation does when it runs, from the run context: see automation_nodes.py for its action and decide nodes).
"""

from __future__ import annotations

import json
import re

from . import (
    analyze,
    automation_nodes,
    classify as classifymod,
    decide,
    fields as fieldmod,
    flow,
    llm,
    metadata,
    organize,
    pipelines,
    store,
    telemetry,
    templates,
    tool_nodes,
)

R = store.R
OWN_NODES = ("llm", "extract_rules", "extract_llm", "classify", "output", "field", "save_entities")
TERMINAL = {"output", "field", "save_entities", "apply_changes"}
OPS = flow.OPS
OWN_CONFIG = {
    "llm": {"template", "version", "model"},
    "extract_rules": {"builtin", "terms", "patterns", "types"},
    "extract_llm": {"types", "instructions", "model"},
    "output": {"key"},
    "field": {"field"},
    "save_entities": set(),
    "classify": {"labels", "vocabulary", "question", "of", "apply"},
}
ENTITY_TYPES = ("PERSON", "ORG", "PRODUCT", "PLACE", "EVENT", "WORK", "TERM", "DATE", "NUMBER")
PATH_RX = flow.PATH_RX
TYPE_RX = re.compile(r"^[A-Z][A-Z_]{0,30}$")
MAX_RULES = 500
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
    "You find named things in a text: people, organisations, products, places, events, works and topics. Use the "
    "names as they are written in the text. Give each one's type and the number of a line it is on. Reply with "
    "JSON only."
)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_graph(db, graph, scope="recording"):
    """The graph, cleaned: ValueError naming the first problem."""
    if scope not in KITS:
        raise ValueError(f"a workflow's scope is {' or '.join(KITS)}")
    return flow.check_graph(db, graph, KITS[scope])


def _check_config(db, nid, t, cfg):
    if t in organize.CONFIG:
        organize.check_config(nid, t, cfg)
    elif t in ("pick", "condition"):
        flow._check_primitive(db, KITS["recording"], nid, t, cfg, None, 0, "")
    elif t == "llm":
        try:
            tpl = templates.get(db, int(cfg.get("template") or 0), cfg.get("version"))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"llm node {nid}: choose a prompt template") from None
        if tpl["kind"] != "prompt":
            raise ValueError(f"llm node {nid} needs a prompt template, not {tpl['kind']}")
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
    elif t == "classify":
        _check_classify(nid, cfg)


def _check_classify(nid, cfg):
    labels, vocab = cfg.get("labels"), cfg.get("vocabulary")
    if (labels is None) == (vocab is None):
        raise ValueError(f"classify node {nid}: give labels, or choose content types or topics")
    try:
        if vocab is not None and vocab not in classifymod.VOCABULARIES:
            raise ValueError(f"choose from {' or '.join(classifymod.VOCABULARIES)}")
        if labels is not None:
            if not isinstance(labels, list):
                raise ValueError("labels are lines like science: research and medicine")
            classifymod._labels(labels)
    except ValueError as e:
        raise ValueError(f"classify node {nid}: {e}") from None
    if cfg.get("question") is not None and not isinstance(cfg["question"], str):
        raise ValueError(f"classify node {nid}: the question is text")
    if cfg.get("of", "resource") not in ("resource", "input"):
        raise ValueError(f"classify node {nid}: it classifies the asset or what comes in")
    if cfg.get("apply") and cfg.get("of") == "input":
        raise ValueError(f"classify node {nid}: only the asset's classification can be acted on")


def _check_types(nid, cfg):
    types = cfg.get("types")
    if types is not None and (not isinstance(types, list) or not all(TYPE_RX.match(str(x)) for x in types)):
        raise ValueError(f"extract node {nid}: types are names in capitals, e.g. PERSON, ORG")


order, dig, test, merge, _is_entity = flow.order, flow.dig, flow.test, flow.merge, flow.is_entity


def create(db, name, graph, description=None, user=None, scope="recording"):
    if not (name or "").strip():
        raise ValueError("give the workflow a name")
    graph = validate_graph(db, graph, scope)
    wid, t = db.next_id("workflow"), store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("workflow", wid),
        d=store.clean(
            {
                "name": name.strip()[:80],
                "description": description,
                "scope": scope,
                "current": 1,
                "created_at": t,
                "updated_at": t,
                "created_by": user,
            }
        ),
    )
    db.q(
        "CREATE $r CONTENT $d",
        r=R("workflow_version", f"{wid}-1"),
        d=store.clean({"workflow": wid, "version": 1, "graph": graph, "created_at": t, "created_by": user}),
    )
    return wid


def save_version(db, wid, graph, notes=None, user=None, publish=True):
    w = db.one("SELECT scope FROM $r", r=R("workflow", wid))
    if not w:
        raise KeyError(wid)
    graph = validate_graph(db, graph, w.get("scope") or "recording")
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
    w = db.one("SELECT record::id(id) AS id, name, description, scope, current, created_at, updated_at FROM $r", r=R("workflow", wid))
    if not w:
        raise KeyError(wid)
    w["scope"] = w.get("scope") or "recording"
    v = db.one("SELECT version, graph, notes, created_at, created_by FROM $r", r=R("workflow_version", f"{wid}-{version or w['current']}"))
    if not v:
        raise KeyError(f"{wid} v{version}")
    return {**w, **v}


def history(db, wid):
    return db.rows("SELECT version, notes, created_at, created_by FROM workflow_version WHERE workflow = $w ORDER BY version DESC", w=wid)


def list_workflows(db, scope=None):
    """Saved workflows, with the pipelines that run each: those of `scope`, or (None) every one but automations'
    (scope event), which are made and changed with their automation."""
    used = {}
    for p in db.rows("SELECT record::id(id) AS id, name, current FROM pipeline"):
        v = db.one("SELECT steps FROM $r", r=R("pipeline_version", f"{p['id']}-{p['current']}")) or {}
        for s in v.get("steps") or []:
            if isinstance(s, dict) and s.get("type") == "workflow" and p["name"] not in used.get(s.get("workflow"), []):
                used.setdefault(s.get("workflow"), []).append(p["name"])
    out = [
        {**w, "scope": w.get("scope") or "recording", "pipelines": used.get(w["id"], [])}
        for w in db.rows("SELECT record::id(id) AS id, name, description, scope, current, updated_at FROM workflow ORDER BY id")
    ]
    return [w for w in out if (w["scope"] == scope if scope else w["scope"] != "event")]


def _segments(db, rid):
    return db.rows("SELECT idx, speaker, text FROM segment WHERE recording = $r ORDER BY idx", r=rid)


def extract_rules(db, cfg, rid, c):
    """Entities found by rules, line by line: [{name, type, seg}] (seg: the line's place in the transcript)."""
    gaz = analyze.parse_gazetteer(
        list(c.get("terms") or []) + (list(cfg["analysis"].get("gazetteer") or []) if c.get("builtin", True) else [])
    )
    pats = [(re.compile(p["pattern"]), p["type"]) for p in c.get("patterns") or []]
    nid = db.one("SELECT VALUE space FROM $r", r=R("recording", rid))
    find = analyze.entity_finder(cfg, gaz, analyze.people_of(db, nid)) if c.get("builtin", True) else None
    types = set(c.get("types") or [])
    out = []
    for k, s in enumerate(_segments(db, rid)):
        text, found = s["text"] or "", []
        for rx, typ in pats:
            found += [(m.group(0), typ) for m in rx.finditer(text) if m.group(0).strip()]
        if find:
            found += find(text)  # as analyze finds them (the rules, or spaCy), your terms included
        else:
            found += [(m.group(0), typ) for _, typ, rx in gaz for m in rx.finditer(text)]
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
    ask.append("Text, one numbered line each:\n" + lines)
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
    attrs = {"lens.workflow.id": str(wid), "lens.workflow.version": version, "lens.recording.id": str(rid)}
    with telemetry.span("workflow", attrs):
        try:
            out = _run(db, cfg, rid, wid, version, say, user)
        except Exception:
            telemetry.record("lens.workflow.runs", 1, {"lens.outcome": "failed"})
            raise
    telemetry.record("lens.workflow.runs", 1, {"lens.outcome": "done"})
    return out


def _run(db, cfg, rid, wid, version, say, user):
    w = get(db, wid, version)
    if w["scope"] != "recording":
        raise ValueError(f"workflow {w['name']} {RUN_BY.get(w['scope'], 'runs elsewhere')}, not a pipeline")
    r = flow.Run(
        db, cfg, KITS["recording"], templates.context(db, cfg, rid), say, user=user,
        origin={"workflow": w["id"], "workflow_version": w["version"]}, rid=rid, by=user or f"workflow:{w['id']}",
    )  # fmt: skip
    flow.run_graph(r, w["graph"])
    return {n["id"]: "done" if r.trace.get(n["id"], {}).get("status") == "done" else "skipped" for n in w["graph"]["nodes"]}


def _run_node(r, f, node, value, vals):
    """What a recording workflow's own nodes do (flow.py runs the rest): {output port: value}."""
    db, cfg, rid, t, c, nid = r.db, r.cfg, r.extra["rid"], node["type"], node["config"], node["id"]
    name, upstream = flow._label(node), None if value is r.ctx else value
    if t == "llm":
        tpl = templates.get(db, int(c["template"]), c.get("version"))
        prompt = templates.render_body(tpl["body"], {**r.ctx, "input": upstream}, "prompt")
        out = llm.json_out(
            cfg, tpl.get("system") or templates.DEFAULT_SYSTEM, prompt, tpl.get("schema") or {"type": "object"}, c.get("model")
        )
        f.made[nid] = {"template": tpl["id"], "version": tpl["version"], "model": c.get("model") or cfg["llm"]["model"]}
        r.say(f"{name}: asked {f.made[nid]['model']}")
        return {"out": out}
    if t == "extract_rules":
        out = extract_rules(db, cfg, rid, c)
        r.say(f"{name}: {len(out)} found")
        return {"out": out}
    if t == "extract_llm":
        so_far = [e for e in upstream if _is_entity(e)] if isinstance(upstream, list) else []
        out = extract_llm(db, cfg, rid, c, so_far)
        r.say(f"{name}: {len(out)} found")
        return {"out": out}
    if t == "output":
        if r.dry:
            r.say(f"would save output {c['key']}")
        else:
            pipelines.save_output(db, rid, c["key"], value, {**r.origin, **f.upstream(nid, f.made)})
            r.say(f"saved output {c['key']}")
    elif t == "field":
        fd = fieldmod.get(db, int(c["field"]))
        defs, row = fieldmod.resource_fields(db, rid)
        try:
            after = fieldmod.merged(row, defs, {fd["id"]: _fit(fd, value)})
        except ValueError as e:
            raise ValueError(f"{name}: {e}") from None
        if r.dry:
            r.say(f"would set {fd['label']}")
        else:
            if after != (row.get("fields") or {}):
                metadata.save_fields(db, cfg, rid, after, r.extra["by"])
            r.say(f"set {fd['label']}")
    elif t == "classify":
        return {"out": _classify(r, c, upstream, name)}
    elif t == "save_entities":
        if not isinstance(value, list):
            raise ValueError(f"{name}: needs a list of entities")
        if r.dry:
            r.say(f"{name}: would keep {len([e for e in value if _is_entity(e)])} entities")
        else:
            r.say(f"{name}: {save_entities(db, cfg, rid, value)} mentions kept")
    return {}


def _classify(r, c, upstream, name):
    """A classify node: the asset, or what came in as text."""
    rid, text, space = r.extra["rid"], None, None
    if c.get("of") == "input":  # its topics are the recording's namespace's
        text = upstream if isinstance(upstream, str) else json.dumps(upstream, ensure_ascii=False, default=str)
        rid, space = None, r.db.values("SELECT VALUE space FROM $r", r=R("recording", int(rid)))[0]
    try:
        res = classifymod.classify(
            r.db, r.cfg, rid=rid, text=text, labels=c.get("labels"), vocab=c.get("vocabulary"), question=c.get("question"),
            apply=bool(c.get("apply")) and not r.dry, user=r.extra.get("by"), space=space,
        )  # fmt: skip
    except decide.Undecided as e:
        raise ValueError(f"{name}: nothing could answer ({e})") from None
    sure = f"{round(res['confidence'] * 100)}% sure"
    if c.get("apply") and r.dry and res["sure"]:
        r.say(f"{name}: {res['choice']} ({sure}); would act on it")
    else:
        r.say(f"{name}: {res['choice']} ({sure}){'; ' + res['applied'].replace('_', ' ') if res['applied'] else ''}")
    return {k: v for k, v in res.items() if k not in ("vocabulary", "question", "applied") and v is not None}


RUN_BY = {"graph": "organises the graph; a routine runs it", "event": "is what an automation does; its automation runs it"}


def try_graph(db, cfg, graph, scope="recording", rid=None, spaces=None, user=None, activity=None):
    """Run a graph without keeping anything (a dry run, from the canvas): {trace, log, error, steps, intents}. Nodes
    that would save something say what they'd save; models are still asked. An automation's workflow (scope event)
    runs on `activity` (an activity, such as a kind's sample) as the person `user` (an account), over `spaces`; its
    actions say what they would do, and `intents` lists them."""
    graph = validate_graph(db, graph, scope)
    log = []
    if scope == "event":
        return _try_event(db, cfg, graph, spaces, user, activity, log)
    if scope == "recording":
        if rid is None:
            raise ValueError("choose an asset to try it on")
        r = flow.Run(db, cfg, KITS[scope], templates.context(db, cfg, rid), log.append, dry=True, user=user, rid=rid, by=user)
    else:
        names = store.space_names(db)
        ctx = {"namespaces": [{"id": s, "name": names.get(s)} for s in sorted(spaces or [])]}
        r = flow.Run(db, cfg, KITS[scope], ctx, log.append, dry=True, user=user, spaces=set(spaces or []), propose_only=True)
    error = None
    try:
        flow.run_graph(r, graph)
    except Exception as e:  # shown on the canvas, on the node that failed
        error = str(e)[:500] or type(e).__name__
    return {"trace": r.trace, "log": log[-200:], "error": error, "steps": r.steps}


def _try_event(db, cfg, graph, spaces, user, activity, log):
    if not isinstance(activity, dict):
        raise ValueError("choose an activity, or a kind of activity, to try it on")
    who = user if isinstance(user, dict) else {}
    auto = {"id": None, "name": "Try", "version": None, "spaces": None if spaces is None else sorted(spaces)}
    ctx = automation_nodes.context(
        db, cfg, auto, {"id": None, "dry": True}, {"as": "event", "on": "event"}, activity, {"event": activity}, who
    )
    box = automation_nodes.Intents(db, cfg, user=who, spaces=auto["spaces"], dry=True)
    r = flow.Run(db, cfg, KITS["event"], ctx, log.append, dry=True, user=who.get("email"), intents=box)
    error = None
    try:
        flow.run_graph(r, graph)
    except Exception as e:  # shown on the canvas, on the node that failed
        error = str(e)[:500] or type(e).__name__
    trace = automation_nodes.finish(r.trace, box)
    return {"trace": trace, "log": log[-200:], "error": error, "steps": r.steps, "intents": box.items}


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


KITS = {
    "recording": flow.Kit(
        "recording", OWN_NODES, OWN_CONFIG, _check_config, _run_node, TERMINAL - {"apply_changes"},
        "a workflow needs an output, field or save entities node (or a classify node that acts), or it keeps nothing",
        keeps=lambda n: n["type"] == "classify" and bool((n.get("config") or {}).get("apply")),
    ),
    "graph": flow.Kit(
        "graph", organize.OWN_NODES, organize.CONFIG, _check_config, organize.run_node, {"apply_changes"},
        "a graph workflow needs an apply changes node, or it changes nothing",
    ),
    # an assistant tool drawn on the canvas (tool_nodes.py): only ever a body, from its parameters to what it gives back
    "tool": flow.Kit(
        "tool", tool_nodes.OWN_NODES, tool_nodes.CONFIG, lambda db, nid, t, cfg: tool_nodes.check_config(db, nid, t, cfg),
        tool_nodes.run_node, set(), "a tool's graph is the body of a tool",
    ),
    # what an automation does (automation_nodes.py): from the run context, its actions and decide nodes
    "event": flow.Kit(
        "event", automation_nodes.OWN_NODES, automation_nodes.CONFIG, automation_nodes.check_config, automation_nodes.run_node,
        set(), automation_nodes.NEEDS, ports=automation_nodes.ports, keeps=automation_nodes.keeps,
        check_graph=automation_nodes.check_graph,
    ),
}  # fmt: skip
SCOPES = {s: k.all_types for s, k in KITS.items()}
RECORDING_NODES = SCOPES["recording"]
NODE_TYPES = RECORDING_NODES + tuple(dict.fromkeys(t for s in ("graph", "tool", "event") for t in SCOPES[s] if t not in RECORDING_NODES))
CONFIG = {**flow.CONFIG, **OWN_CONFIG, **organize.CONFIG, **tool_nodes.CONFIG, **automation_nodes.CONFIG}
