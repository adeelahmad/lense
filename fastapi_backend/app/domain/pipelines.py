"""Pipelines: named, versioned lists of steps a recording goes through. Each namespace can choose its default; without
one, recordings get the standard pipeline. Steps: transcribe, diarize, analyze, embed (passages for search by meaning,
semantic.py), summarize, llm (a prompt template whose structured result is saved as a named output), report (built in,
or from a report template) and export (a template rendered to a file, optionally copied to a storage source) and
workflow (a workflow graph, workflows.py, that turns what the steps made into metadata). Any step can carry a
condition.

Which pipeline a recording gets also depends on its content type (content_types.py): the one chosen for the run, else
the namespace's override for the recording's subtype, else the subtype's own pipeline, else the namespace's default,
else the standard pipeline.

A version can be saved as a graph, as the canvas draws it: `{nodes: [{id, step, x, y}], edges: [{source, target}]}`,
an edge saying its target runs after its source. It is put in order (each step after the ones it follows, ties left to
right on the canvas) and kept as the version's steps, so runs work as they always have. A version saved as a plain list
of steps is drawn as a chain."""

from __future__ import annotations

import pathlib
import re

from . import llm, render, sources, store, templates

R = store.R
STANDARD = ["transcribe", "diarize", "shots", "ocr", "faces", "objects", "describe", "analyze", "embed", "classify", "summarize", "report"]
TYPES = {
    "transcribe", "diarize", "shots", "ocr", "faces", "objects", "describe", "analyze", "embed", "classify", "summarize", "llm", "report", "export",
    "workflow",
}  # fmt: skip
ASSET_STEPS = ("transcribe", "diarize", "shots", "ocr", "faces", "objects", "describe")  # they make something of the media
KEYS = {"type", "name", "when", "template", "version", "key", "filename", "destination", "model", "force", "workflow"}
NODE_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
WHEN = {"min_minutes": (int, float), "max_minutes": (int, float), "source": str, "languages": list}
KEY_RX = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def validate_steps(db, steps):
    out = []
    for s in steps or []:
        s = {"type": s} if isinstance(s, str) else dict(s or {})
        t = s.get("type")
        if t not in TYPES:
            raise ValueError(f"step type is one of {', '.join(sorted(TYPES))}")
        extra = sorted(set(s) - KEYS)
        if extra:
            raise ValueError(f"{t} step has no setting {extra[0]}")
        for k, v in (s.get("when") or {}).items():
            if k not in WHEN or not isinstance(v, WHEN[k]):
                raise ValueError(f"conditions are {', '.join(WHEN)}")
        if t == "workflow":
            from . import workflows

            try:
                w = workflows.get(db, int(s.get("workflow") or 0), s.get("version"))
            except (KeyError, TypeError, ValueError):
                raise ValueError("workflow step: choose a workflow") from None
            if w["scope"] != "recording":
                raise ValueError(f"workflow step: {w['name']} organises the graph; run it from a routine")
        need = {"llm": "prompt", "export": "export", "report": "report"}.get(t)
        if need and (s.get("template") or t != "report"):
            try:
                tpl = templates.get(db, int(s.get("template") or 0), s.get("version"))
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"{t} step: choose a {need} template") from None
            if tpl["kind"] != need:
                raise ValueError(f"{t} step needs a {need} template, not {tpl['kind']}")
        if t == "llm" and not KEY_RX.match(str(s.get("key") or "")):
            raise ValueError("llm step: name the output with lowercase letters, digits and _ (e.g. meeting_notes)")
        if t == "export":
            if not str(s.get("filename") or "").strip():
                raise ValueError("export step: give a file name, e.g. {{ recording.title }}.md")
            dest = s.get("destination")
            if dest and not (isinstance(dest, dict) and isinstance(dest.get("source"), int) and isinstance(dest.get("path", ""), str)):
                raise ValueError("export destination is {source: id, path: folder}")
        out.append(s)
    if not out:
        raise ValueError("a pipeline needs at least one step")
    return out


def validate_graph(db, graph):
    """(the graph cleaned, its steps in the order they run): ValueError naming the first problem."""
    graph = dict(graph or {})
    nodes, edges = graph.get("nodes") or [], graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("a pipeline graph is {nodes: [...], edges: [...]}")
    clean, by_id = [], {}
    for n in nodes:
        n = dict(n or {})
        nid = str(n.get("id") or "")
        if not NODE_RX.match(nid) or nid in by_id:
            raise ValueError("each node has its own id of 1 to 40 letters, digits, _ and -")
        step = validate_steps(db, [n.get("step")])[0]
        c = {"id": nid, "step": step}
        for k in ("x", "y"):
            if isinstance(n.get(k), (int, float)) and not isinstance(n.get(k), bool):
                c[k] = round(float(n[k]), 1)
        by_id[nid] = c
        clean.append(c)
    out_edges = []
    for e in edges:
        a, b = str((e or {}).get("source") or ""), str((e or {}).get("target") or "")
        if a not in by_id or b not in by_id or a == b:
            raise ValueError("an edge joins two steps of the pipeline")
        if {"source": a, "target": b} not in out_edges:
            out_edges.append({"source": a, "target": b})
    ins = {n["id"]: 0 for n in clean}
    for e in out_edges:
        ins[e["target"]] += 1
    pos = {n["id"]: (n.get("x", 0), n.get("y", 0), k) for k, n in enumerate(clean)}
    ready, ordered = sorted((i for i, c in ins.items() if c == 0), key=pos.get), []
    while ready:
        n = ready.pop(0)
        ordered.append(n)
        for e in out_edges:
            if e["source"] == n:
                ins[e["target"]] -= 1
                if ins[e["target"]] == 0:
                    ready = sorted(ready + [e["target"]], key=pos.get)
    if len(ordered) != len(clean):
        raise ValueError("the pipeline has a loop")
    if not ordered:
        raise ValueError("a pipeline needs at least one step")
    return {"nodes": clean, "edges": out_edges}, [by_id[i]["step"] for i in ordered]


def chain(steps):
    """A list of steps drawn as a graph: one after the other, left to right."""
    nodes = [
        {"id": f"s{k + 1}", "step": s if isinstance(s, dict) else {"type": s}, "x": 40 + 240 * k, "y": 80} for k, s in enumerate(steps)
    ]
    return {"nodes": nodes, "edges": [{"source": a["id"], "target": b["id"]} for a, b in zip(nodes, nodes[1:])]}


def _steps_and_graph(db, steps, graph):
    if graph is not None:
        return validate_graph(db, graph)[::-1]
    return validate_steps(db, steps), None


def create(db, name, steps, description=None, user=None, graph=None):
    if not (name or "").strip():
        raise ValueError("give the pipeline a name")
    steps, graph = _steps_and_graph(db, steps, graph)
    pid = db.next_id("pipeline")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("pipeline", pid),
        d=store.clean(
            {
                "name": name.strip()[:80],
                "description": description,
                "current": 1,
                "created_at": store.now(),
                "updated_at": store.now(),
                "created_by": user,
            }
        ),
    )
    db.q(
        "CREATE $r CONTENT $d",
        r=R("pipeline_version", f"{pid}-1"),
        d=store.clean({"pipeline": pid, "version": 1, "steps": steps, "graph": graph, "created_at": store.now(), "created_by": user}),
    )
    return pid


def save_version(db, pid, steps, notes=None, user=None, publish=True, graph=None):
    if not db.one("SELECT id FROM $r", r=R("pipeline", pid)):
        raise KeyError(pid)
    steps, graph = _steps_and_graph(db, steps, graph)
    n = max(db.values("SELECT VALUE version FROM pipeline_version WHERE pipeline = $p", p=pid) or [0]) + 1
    db.q(
        "CREATE $r CONTENT $d",
        r=R("pipeline_version", f"{pid}-{n}"),
        d=store.clean(
            {"pipeline": pid, "version": n, "steps": steps, "graph": graph, "notes": notes, "created_at": store.now(), "created_by": user}
        ),
    )
    db.q("UPDATE $r SET updated_at = $t" + (", current = $n" if publish else ""), r=R("pipeline", pid), t=store.now(), n=n)
    return n


def get(db, pid, version=None):
    p = db.one("SELECT record::id(id) AS id, name, description, current, created_at, updated_at FROM $r", r=R("pipeline", pid))
    if not p:
        raise KeyError(pid)
    v = db.one(
        "SELECT version, steps, graph, notes, created_at, created_by FROM $r", r=R("pipeline_version", f"{pid}-{version or p['current']}")
    )
    if not v:
        raise KeyError(f"{pid} v{version}")
    return {**p, **v, "graph": v.get("graph") or chain(v["steps"])}


def list_pipelines(db):
    from . import content_types

    used, typed, sub = {}, {}, {}
    for s in db.rows("SELECT name, pipeline, pipelines FROM space WHERE pipeline != NONE OR pipelines != NONE"):
        if s.get("pipeline"):
            used.setdefault(s["pipeline"], []).append(s["name"])
        for kind, pid in (s.get("pipelines") or {}).items():
            typed.setdefault(pid, []).append({"namespace": s["name"], "content_type": kind})
    for t in content_types.all_types(db):
        if t.get("pipeline"):
            sub.setdefault(t["pipeline"], []).append(t["key"])
    return [
        {**p, "namespaces": used.get(p["id"], []), "content_types": typed.get(p["id"], []), "subtypes": sub.get(p["id"], [])}
        for p in db.rows("SELECT record::id(id) AS id, name, description, current, updated_at FROM pipeline ORDER BY id")
    ]


def set_content_types(db, space, mapping):
    """A namespace's own pipeline per content subtype ({subtype: pipeline id, or None to drop the override})."""
    from . import content_types

    keys = {t["key"] for t in content_types.all_types(db)}
    cur = dict((db.one("SELECT pipelines FROM $s", s=R("space", space)) or {}).get("pipelines") or {})
    for kind, pid in (mapping or {}).items():
        if kind not in keys:
            raise ValueError(f"no content type {kind}")
        if pid is None:
            cur.pop(kind, None)
            continue
        try:
            get(db, int(pid))
        except (KeyError, TypeError, ValueError):
            raise ValueError("no such pipeline") from None
        cur[kind] = int(pid)
    db.q("UPDATE $s SET pipelines = $m", s=R("space", space), m=cur or None)
    return cur


def resolve(db, space, pipeline_id=None, content_type=None):
    """(steps, which pipeline): the one asked for, else the namespace's for the content subtype, else the subtype's,
    else the namespace's default, else the standard one. `content_type` is a subtype ({key, pipeline}) or its key."""
    from . import content_types

    sp = db.one("SELECT pipeline, pipelines FROM $s", s=R("space", space)) or {}
    if isinstance(content_type, str):
        try:
            content_type = content_types.get(db, content_type)
        except KeyError:
            content_type = None
    key = (content_type or {}).get("key") or ""
    pid = pipeline_id or (sp.get("pipelines") or {}).get(key) or (content_type or {}).get("pipeline") or sp.get("pipeline")
    if pid:
        p = get(db, int(pid))
        return pin(db, p["steps"]), {"id": p["id"], "version": p["version"], "name": p["name"]}
    return [{"type": t} for t in STANDARD], {"id": None, "version": None, "name": "Standard"}


def pin(db, steps):
    """Steps with each workflow pinned to the version current now, so a run keeps to what it started with."""
    from . import workflows

    out = []
    for s in steps:
        if isinstance(s, dict) and s.get("type") == "workflow" and not s.get("version"):
            s = {**s, "version": workflows.get(db, int(s["workflow"]))["version"]}
        out.append(s)
    return out


def condition_ok(db, rid, when):
    rec = db.one("SELECT duration_ms, source, language FROM $r", r=R("recording", rid)) or {}
    mins = (rec.get("duration_ms") or 0) / 60000
    return not (
        ("min_minutes" in when and mins < when["min_minutes"])
        or ("max_minutes" in when and mins > when["max_minutes"])
        or ("source" in when and rec.get("source") != when["source"])
        or (when.get("languages") and rec.get("language") not in when["languages"])
    )


def save_output(db, rid, key, value, origin=None):
    db.q(
        "UPSERT $o CONTENT $d",
        o=R("output", f"{rid}-{key}"),
        d=store.clean({"recording": rid, "key": key, "value": value, "origin": origin, "created_at": store.now()}),
    )


def run_llm(db, cfg, rid, spec, say):
    t = templates.get(db, int(spec["template"]), spec.get("version"))
    prompt = templates.render_body(t["body"], templates.context(db, cfg, rid), "prompt")
    value = llm.json_out(cfg, t.get("system") or templates.DEFAULT_SYSTEM, prompt, t.get("schema") or {"type": "object"}, spec.get("model"))
    save_output(
        db, rid, spec["key"], value, {"template": t["id"], "version": t["version"], "model": spec.get("model") or cfg["llm"]["model"]}
    )
    say(f"saved output {spec['key']}")


def _space_name(db, rid):
    rec = db.one("SELECT space, title FROM $r", r=R("recording", rid))
    return (db.one("SELECT name FROM $s", s=R("space", rec["space"])) or {}).get("name"), rec.get("title")


def run_report(db, cfg, rid, spec, say):
    t = templates.get(db, int(spec["template"]), spec.get("version"))
    html = templates.render_body(t["body"], templates.context(db, cfg, rid), "report")
    ns, title = _space_name(db, rid)
    out = pathlib.Path(cfg["data_dir"]) / "reports" / ns / f"{render.slug(title)}-{rid}--{render.slug(t['name'])}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    save_output(
        db,
        rid,
        f"report_{render.slug(t['name']).replace('-', '_')}",
        {"url": f"/reports/{ns}/{out.name}"},
        {"template": t["id"], "version": t["version"]},
    )
    say(f"wrote {out.name}")


def run_export(db, cfg, rid, spec, say):
    t = templates.get(db, int(spec["template"]), spec.get("version"))
    ctx = templates.context(db, cfg, rid)
    name = pathlib.PurePosixPath(templates.render_body(spec["filename"], ctx, "export").strip()).name
    name = re.sub(r"[^\w .()-]+", "_", name)[:120].strip() or f"recording-{rid}.txt"
    ns, _ = _space_name(db, rid)
    out = pathlib.Path(cfg["data_dir"]) / "exports" / ns / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(templates.render_body(t["body"], ctx, "export", name), encoding="utf-8")
    uploaded = None
    dest = spec.get("destination")
    if dest:
        src = sources.get(db, dest["source"])
        target = sources.check_path(cfg, src, str(pathlib.PurePosixPath(dest.get("path") or "") / name))
        sources.run(db, cfg, src, lambda n: ["copyto", str(out), f"{n}:{target}"], timeout=600)
        uploaded = f"{src['name']}:{target}"
    save_output(
        db,
        rid,
        "export_" + re.sub(r"\W+", "_", name.lower()).strip("_")[:40],
        {"file": name, "uploaded_to": uploaded},
        {"template": t["id"], "version": t["version"]},
    )
    say(f"exported {name}" + (f" to {uploaded}" if uploaded else ""))
