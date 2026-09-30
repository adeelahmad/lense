"""Templates written in the app: prompt templates (for LLM steps, with an output schema) and report or export
templates (HTML or text). Versioned; rendered with a sandboxed Jinja environment that can't reach Python internals,
can't build huge strings, and escapes HTML in reports."""
from __future__ import annotations

import difflib
import json

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

from . import render, store

R = store.R
KINDS = ("prompt", "report", "export")
LIMIT = 2_000_000
DEFAULT_SYSTEM = ("You analyse transcripts of recorded conversations. Be accurate and specific, and don't invent anything "
                  "that isn't in the transcript. Reply with JSON only.")


class TemplateProblem(ValueError):
    pass


class _Env(SandboxedEnvironment):
    intercepted_binops = frozenset(["*", "**"])

    def call_binop(self, context, operator, left, right):
        if operator == "**" and isinstance(right, (int, float)) and abs(right) > 64:
            raise SecurityError("exponent too large")
        if operator == "*":
            for x, n in ((left, right), (right, left)):
                if isinstance(x, (str, list, tuple)) and isinstance(n, int) and len(x) * n > 1_000_000:
                    raise SecurityError("result too large")
        return super().call_binop(context, operator, left, right)


_ENVS = {}


def _env(escape):
    if escape not in _ENVS:
        e = _Env(autoescape=escape, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
        e.filters["tc"] = store.tc
        e.filters["json"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
        _ENVS[escape] = e
    return _ENVS[escape]


def check(body):
    try:
        _env(False).parse(body)
    except TemplateSyntaxError as e:
        raise TemplateProblem(f"line {e.lineno}: {e.message}") from None


def render_body(body, ctx, kind, filename=None):
    try:
        out = _env(kind == "report" or (filename or "").lower().endswith(".html")).from_string(body).render(**ctx)
    except SecurityError as e:
        raise TemplateProblem(f"not allowed in templates: {e}") from None
    except TemplateSyntaxError as e:
        raise TemplateProblem(f"line {e.lineno}: {e.message}") from None
    except (UndefinedError, TypeError, ValueError) as e:
        raise TemplateProblem(str(e)) from None
    if len(out) > LIMIT:
        raise TemplateProblem("the output is too large")
    return out


def context(db, cfg, rid):
    """What a template can use for one recording."""
    d = render.player_data(db, rid)
    rec = db.one("SELECT recorded_at, duration_ms, language, source, status, summary FROM $r", r=R("recording", rid)) or {}
    names = {sp["key"]: sp["name"] for sp in d["speakers"]}
    segs = [{"t0": s["t0"], "t1": s["t1"], "time": store.tc(s["t0"]), "speaker": names.get(s["s"], "Unknown"), "text": s["text"],
             "emotion": s.get("e"), "event": s.get("v")} for s in d["segments"]]
    full = "\n".join(f"[{s['time']}] {s['speaker']}: {s['text']}" for s in segs)
    cap = cfg["llm"].get("max_chars") or 24000
    stats = render.recording_stats(db, rid)
    outputs = {o["key"]: o["value"] for o in db.rows("SELECT key, value FROM output WHERE recording = $r", r=rid)}
    return {"recording": {"id": rid, "title": d["title"], "namespace": d["namespace"], "recorded_at": rec.get("recorded_at"),
                          "duration": store.tc(d["duration_ms"]), "duration_ms": d["duration_ms"], "language": rec.get("language"),
                          "source": rec.get("source"), "status": rec.get("status")},
            "speakers": [{"name": s.get("name"), "talk_ms": s.get("talk_ms"), "words": s.get("words"), "wpm": s.get("wpm")} for s in stats.get("speakers", [])],
            "segments": segs, "full_transcript": full, "transcript": full if len(full) <= cap else full[:cap] + "\n[… transcript truncated]",
            "sections": [{"title": x["title"], "time": store.tc(x["t0"]), "t0": x["t0"]} for x in d["sections"]],
            "entities": [{"name": e["name"], "type": e["type"], "mentions": len(e["segs"])} for e in d["entities"]],
            "keywords": [w for w, _ in d["keywords"]], "summary": rec.get("summary") or {}, "stats": stats, "outputs": outputs}


# ---------- storage ----------
def _clean_schema(kind, schema):
    if kind != "prompt":
        return None
    schema = schema or {"type": "object"}
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise TemplateProblem("the output schema must be a JSON Schema with type object")
    return schema


def create(db, name, kind, body, schema=None, system=None, description=None, user=None):
    if kind not in KINDS:
        raise TemplateProblem(f"kind is one of {', '.join(KINDS)}")
    if not (name or "").strip():
        raise TemplateProblem("give the template a name")
    check(body)
    tid = db.next_id("template")
    db.q("CREATE $r CONTENT $d", r=R("template", tid), d=store.clean({"name": name.strip()[:80], "kind": kind, "description": description,
                                                                      "current": 1, "created_at": store.now(), "updated_at": store.now(), "created_by": user}))
    db.q("CREATE $r CONTENT $d", r=R("template_version", f"{tid}-1"), d=store.clean({"template": tid, "version": 1, "body": body,
                                                                                     "schema": _clean_schema(kind, schema), "system": system,
                                                                                     "created_at": store.now(), "created_by": user}))
    return tid


def save_version(db, tid, body, schema=None, system=None, notes=None, user=None, publish=True):
    t = db.one("SELECT kind FROM $r", r=R("template", tid))
    if not t:
        raise KeyError(tid)
    check(body)
    n = max(db.values("SELECT VALUE version FROM template_version WHERE template = $t", t=tid) or [0]) + 1
    db.q("CREATE $r CONTENT $d", r=R("template_version", f"{tid}-{n}"), d=store.clean({"template": tid, "version": n, "body": body,
                                                                                       "schema": _clean_schema(t["kind"], schema), "system": system,
                                                                                       "notes": notes, "created_at": store.now(), "created_by": user}))
    db.q("UPDATE $r SET updated_at = $t" + (", current = $n" if publish else ""), r=R("template", tid), t=store.now(), n=n)
    return n


def get(db, tid, version=None):
    t = db.one("SELECT record::id(id) AS id, name, kind, description, current, created_at, updated_at FROM $r", r=R("template", tid))
    if not t:
        raise KeyError(tid)
    v = db.one("SELECT version, body, schema, system, notes, created_at, created_by FROM $r", r=R("template_version", f"{tid}-{version or t['current']}"))
    if not v:
        raise KeyError(f"{tid} v{version}")
    return {**t, **v}


def list_templates(db):
    counts = {}
    for v in db.rows("SELECT template FROM template_version"):
        counts[v["template"]] = counts.get(v["template"], 0) + 1
    return [{**t, "versions": counts.get(t["id"], 0)} for t in db.rows(
        "SELECT record::id(id) AS id, name, kind, description, current, updated_at FROM template ORDER BY id")]


def versions(db, tid):
    return db.rows("SELECT version, notes, created_at, created_by FROM template_version WHERE template = $t ORDER BY version DESC", t=tid)


def diff(db, tid, a, b):
    x, y = get(db, tid, a)["body"], get(db, tid, b)["body"]
    return "".join(difflib.unified_diff(x.splitlines(True), y.splitlines(True), f"version {a}", f"version {b}"))


SEEDS = [
    ("Meeting notes", "prompt", "TL;DR, decisions, action items and open questions.",
     'Summarise "{{ recording.title }}" ({{ recording.duration }}).\n\nSpeakers: {% for s in speakers %}{{ s.name }}{% if not loop.last %}, {% endif %}{% endfor %}\n\n'
     "Transcript:\n{{ transcript }}\n",
     {"type": "object", "required": ["tldr", "decisions", "action_items", "open_questions"],
      "properties": {"tldr": {"type": "string"}, "decisions": {"type": "array", "items": {"type": "string"}},
                     "action_items": {"type": "array", "items": {"type": "object", "required": ["task"],
                                                                 "properties": {"owner": {"type": "string"}, "task": {"type": "string"}, "due": {"type": "string"}}}},
                     "open_questions": {"type": "array", "items": {"type": "string"}}}}),
    ("Markdown transcript", "export", "The transcript as Markdown, one paragraph per turn.",
     "# {{ recording.title }}\n\n{{ recording.recorded_at }} · {{ recording.duration }}\n\n{% for s in segments %}**{{ s.speaker }}** ({{ s.time }}): {{ s.text }}\n\n{% endfor %}", None),
    ("One-page brief", "report", "A printable page: summary, action items, speakers and topics.",
     '<!doctype html><html><head><meta charset="utf-8"><title>{{ recording.title }}</title>\n<style>body{font:15px/1.55 Georgia,serif;max-width:46rem;margin:2rem auto;color:#1d2733}'
     "h1{font-size:1.6rem}table{border-collapse:collapse}td{padding:2px 12px 2px 0}.muted{color:#667}</style></head><body>\n"
     '<h1>{{ recording.title }}</h1><p class="muted">{{ recording.namespace }} · {{ recording.recorded_at }} · {{ recording.duration }}</p>\n'
     "{% set notes = outputs.get('meeting_notes', {}) %}{% if notes %}<h2>Summary</h2><p>{{ notes.tldr }}</p>\n"
     "{% if notes.action_items %}<h2>Action items</h2><ul>{% for a in notes.action_items %}<li>{{ a.task }}{% if a.owner %} — {{ a.owner }}{% endif %}</li>{% endfor %}</ul>{% endif %}{% endif %}\n"
     "<h2>Speakers</h2><table>{% for s in speakers %}<tr><td>{{ s.name }}</td><td>{{ (s.talk_ms or 0) | tc }}</td><td>{{ s.words }} words</td></tr>{% endfor %}</table>\n"
     '<h2>Topics</h2><p>{{ keywords[:15] | join(", ") }}</p></body></html>\n', None),
]


def seed(db):
    """Starter templates on a fresh archive."""
    if db.values("SELECT VALUE id FROM template LIMIT 1"):
        return
    for name, kind, desc, body, schema in SEEDS:
        create(db, name, kind, body, schema, description=desc, user="lens-archive")
