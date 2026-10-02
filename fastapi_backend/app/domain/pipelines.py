"""Pipelines: named, versioned lists of steps a recording goes through. Each namespace can choose its default; without
one, recordings get the standard pipeline. Steps: transcribe, diarize, analyze, summarize, llm (a prompt template whose
structured result is saved as a named output), report (built in, or from a report template) and export (a template
rendered to a file, optionally copied to a storage source). Any step can carry a condition."""

from __future__ import annotations

import pathlib
import re

from . import llm, render, sources, store, templates

R = store.R
STANDARD = ["transcribe", "diarize", "shots", "ocr", "faces", "objects", "describe", "embed", "analyze", "summarize", "report"]
TYPES = {
    "transcribe",
    "diarize",
    "shots",
    "ocr",
    "faces",
    "objects",
    "describe",
    "embed",
    "analyze",
    "summarize",
    "llm",
    "report",
    "export",
}
KEYS = {"type", "name", "when", "template", "version", "key", "filename", "destination", "model", "force"}
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


def create(db, name, steps, description=None, user=None):
    if not (name or "").strip():
        raise ValueError("give the pipeline a name")
    steps = validate_steps(db, steps)
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
        d=store.clean({"pipeline": pid, "version": 1, "steps": steps, "created_at": store.now(), "created_by": user}),
    )
    return pid


def save_version(db, pid, steps, notes=None, user=None, publish=True):
    if not db.one("SELECT id FROM $r", r=R("pipeline", pid)):
        raise KeyError(pid)
    steps = validate_steps(db, steps)
    n = max(db.values("SELECT VALUE version FROM pipeline_version WHERE pipeline = $p", p=pid) or [0]) + 1
    db.q(
        "CREATE $r CONTENT $d",
        r=R("pipeline_version", f"{pid}-{n}"),
        d=store.clean({"pipeline": pid, "version": n, "steps": steps, "notes": notes, "created_at": store.now(), "created_by": user}),
    )
    db.q("UPDATE $r SET updated_at = $t" + (", current = $n" if publish else ""), r=R("pipeline", pid), t=store.now(), n=n)
    return n


def get(db, pid, version=None):
    p = db.one("SELECT record::id(id) AS id, name, description, current, created_at, updated_at FROM $r", r=R("pipeline", pid))
    if not p:
        raise KeyError(pid)
    v = db.one("SELECT version, steps, notes, created_at, created_by FROM $r", r=R("pipeline_version", f"{pid}-{version or p['current']}"))
    if not v:
        raise KeyError(f"{pid} v{version}")
    return {**p, **v}


def list_pipelines(db):
    used = {}
    for s in db.rows("SELECT name, pipeline FROM space WHERE pipeline != NONE"):
        used.setdefault(s["pipeline"], []).append(s["name"])
    return [
        {**p, "namespaces": used.get(p["id"], [])}
        for p in db.rows("SELECT record::id(id) AS id, name, description, current, updated_at FROM pipeline ORDER BY id")
    ]


def resolve(db, space, pipeline_id=None):
    pid = pipeline_id or (db.one("SELECT pipeline FROM $s", s=R("space", space)) or {}).get("pipeline")
    if pid:
        p = get(db, int(pid))
        return p["steps"], {"id": p["id"], "version": p["version"], "name": p["name"]}
    return [{"type": t} for t in STANDARD], {"id": None, "version": None, "name": "Standard"}


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
