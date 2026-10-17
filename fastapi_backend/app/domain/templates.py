"""Templates written in the app: prompt templates (for LLM steps, with an output schema) and report or export
templates (HTML or text). Versioned; rendered with a sandboxed Jinja environment that can't reach Python internals,
can't build huge strings, and escapes HTML in reports. Automations render their params and prompts with the same
sandbox (variables.py); the filters below (date, ago, local, plain, emails, defang) are there for every template."""

from __future__ import annotations

import datetime as dt
import difflib
import functools
import json
import re

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError, pass_context
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

from . import analyze, render, schedule, store, untrusted

R = store.R
KINDS = ("prompt", "report", "export")
PARAM = "param"  # how automations render their params and prompts (variables.py); not a kind templates are saved as
LIMIT = 2_000_000
DEFAULT_SYSTEM = (
    "You analyse the assets of a personal archive: transcripts of recordings, documents and other text. Be accurate "
    "and specific, and don't invent anything that isn't in the text. Reply with JSON only."
)


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


class _DataEnv(_Env):
    """The sandbox for automations' params and prompts (kind `param`). a.b reads a dict's key b before a method of the
    same name, so an action's output {"items": [...]} reads as steps.n3.items, not as dict.items. Outside text marked
    as data (untrusted.Marked) stays marked through filters and string methods: they work on its words and what they
    give is marked again (`plain`, `truncate`, `upper`... can't drop or cut its markers)."""

    def getattr(self, obj, attribute):
        if isinstance(obj, dict) and isinstance(attribute, str) and attribute in obj:
            return obj[attribute]
        return super().getattr(obj, attribute)

    def call(self, context, obj, /, *args, **kwargs):
        owner = getattr(obj, "__self__", None)
        if isinstance(owner, untrusted.Marked) and isinstance(getattr(obj, "__name__", None), str):
            return owner._again(super().call(context, getattr(owner._text, obj.__name__), *args, **kwargs))
        return super().call(context, obj, *args, **kwargs)


def _marked_aware(fn):
    """A filter that, given marked outside text (or a list holding some), works on its words and marks what it gives
    again, with the source of the first marked piece."""
    skip = 1 if getattr(fn, "jinja_pass_arg", None) else 0

    @functools.wraps(fn)
    def run(*args, **kwargs):
        v = args[skip] if len(args) > skip else None
        if isinstance(v, untrusted.Marked):
            return v._again(fn(*args[:skip], v._text, *args[skip + 1 :], **kwargs))
        if isinstance(v, (list, tuple)) and any(isinstance(x, untrusted.Marked) for x in v):
            first = next(x for x in v if isinstance(x, untrusted.Marked))
            words = [x._text if isinstance(x, untrusted.Marked) else x for x in v]
            return first._again(fn(*args[:skip], words, *args[skip + 1 :], **kwargs))
        return fn(*args, **kwargs)

    return run


_ENVS = {}


# ---------- filters for times, text and addresses ----------
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
MAX_FORMAT = 200


def when(v):
    """A time from what a template holds: a datetime or date, epoch seconds (or milliseconds), or ISO 8601 text (Z or
    an offset; none means UTC; a date alone stays a date). ValueError for anything else."""
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return dt.datetime.fromtimestamp(v / 1000 if abs(v) > 1e11 else v, dt.timezone.utc)
    text = str(v or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return dt.date.fromisoformat(text)
        d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{text[:60] or 'nothing'} isn't a time") from None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _zone(context, tz):
    return schedule.zone(tz or (context.get("timezone") if context is not None else None) or "UTC")


def _now(context):
    got = context.get("now") if context is not None else None
    try:
        return when(got) if got else dt.datetime.now(dt.timezone.utc)
    except ValueError:
        return dt.datetime.now(dt.timezone.utc)


@pass_context
def f_date(context, v, fmt="%Y-%m-%d %H:%M", tz=None):
    """A time written with a strftime format, in `tz` (default the template's `timezone`, else UTC)."""
    fmt = str(fmt)
    if len(fmt) > MAX_FORMAT:
        raise ValueError(f"date: the format is at most {MAX_FORMAT} characters")
    d = when(v)
    if isinstance(d, dt.datetime):
        d = d.astimezone(_zone(context, tz))
    return d.strftime(fmt)


@pass_context
def f_local(context, v, tz=None):
    """A time in the template's time zone (`timezone`, else UTC), as ISO 8601 with its offset."""
    d = when(v)
    return d.astimezone(_zone(context, tz)).isoformat(timespec="seconds") if isinstance(d, dt.datetime) else d.isoformat()


def _span(seconds):
    for unit, n in (("year", 31_536_000), ("month", 2_592_000), ("week", 604_800), ("day", 86_400), ("hour", 3600), ("minute", 60)):
        if seconds >= n:
            k = int(seconds // n)
            return f"{k} {unit}{'' if k == 1 else 's'}"
    return f"{int(seconds)} second{'' if int(seconds) == 1 else 's'}"


@pass_context
def f_ago(context, v):
    """How long ago a time was, from the template's `now` (else now): "3 hours ago", "in 2 days", "just now"."""
    d = when(v)
    if not isinstance(d, dt.datetime):
        d = dt.datetime.combine(d, dt.time(), dt.timezone.utc)
    seconds = (_now(context) - d).total_seconds()
    if abs(seconds) < 45:
        return "just now"
    return f"{_span(seconds)} ago" if seconds > 0 else f"in {_span(-seconds)}"


def f_plain(v):
    """HTML as plain text: tags, scripts and styles out, one line per block. Markers around outside text stay."""
    from . import mailbox

    kept = []

    def keep(m):
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"

    text = mailbox.html_text(untrusted.MARKER_RX.sub(keep, str(v or "")))
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


def f_emails(v):
    """The email addresses in text (or in a list of texts), lowercased, each once, in order."""
    text = " ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v or "")
    return list(dict.fromkeys(m.lower() for m in EMAIL_RX.findall(text)))


FILTERS = {
    "date": f_date,
    "local": f_local,
    "ago": f_ago,
    "plain": f_plain,
    "emails": f_emails,
    "defang": lambda v: untrusted.defang(str(v or "")),
}


def _env(escape, data=False):
    """The sandbox: HTML escaped with `escape`; with `data`, dict keys before dict methods (_DataEnv)."""
    if (escape, data) not in _ENVS:
        e = (_DataEnv if data else _Env)(autoescape=escape, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
        e.filters["tc"] = store.tc
        e.filters["json"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
        for name, fn in FILTERS.items():
            e.filters.setdefault(name, fn)  # never in place of a filter templates already use
        if data:
            e.filters = {name: _marked_aware(fn) for name, fn in e.filters.items()}
        _ENVS[(escape, data)] = e
    return _ENVS[(escape, data)]


def check(body):
    try:
        _env(False).parse(body)
    except TemplateSyntaxError as e:
        raise TemplateProblem(f"line {e.lineno}: {e.message}") from None


def render_body(body, ctx, kind, filename=None):
    """`body` rendered with `ctx`: HTML escaped for reports and .html exports; kind `param` (an automation's params and
    prompts) reads dict keys before dict methods. TemplateProblem naming the problem."""
    escape = kind == "report" or (filename or "").lower().endswith(".html")
    try:
        out = _env(escape, kind == PARAM).from_string(body).render(**ctx)
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
    segs = [
        {
            "t0": s["t0"],
            "t1": s["t1"],
            "time": store.tc(s["t0"]),
            "speaker": names.get(s["s"], "Unknown"),
            "text": s["text"],
            "emotion": s.get("e"),
            "event": s.get("v"),
        }
        for s in d["segments"]
    ]
    full = "\n".join(f"[{s['time']}] {s['speaker']}: {s['text']}" for s in segs)
    cap = cfg["llm"].get("max_chars") or 24000
    stats = render.recording_stats(db, rid)
    outputs = {o["key"]: o["value"] for o in db.rows("SELECT key, value FROM output WHERE recording = $r", r=rid)}
    return {
        "recording": {
            "id": rid,
            "title": d["title"],
            "namespace": d["namespace"],
            "recorded_at": rec.get("recorded_at"),
            "duration": store.tc(d["duration_ms"]),
            "duration_ms": d["duration_ms"],
            "language": rec.get("language"),
            "source": rec.get("source"),
            "status": rec.get("status"),
        },
        "speakers": [
            {"name": s.get("name"), "talk_ms": s.get("talk_ms"), "words": s.get("words"), "wpm": s.get("wpm")}
            for s in stats.get("speakers", [])
        ],
        "segments": segs,
        "full_transcript": full,
        "transcript": full if len(full) <= cap else full[:cap] + "\n[… text truncated]",
        "sections": [{"title": x["title"], "time": store.tc(x["t0"]), "t0": x["t0"]} for x in d["sections"]],
        "entities": [{"name": e["name"], "type": e["type"], "mentions": len(e["segs"])} for e in d["entities"]],
        "keywords": [w for w, _ in d["keywords"]],
        "summary": analyze.summary_view(rec.get("summary")),
        "stats": stats,
        "outputs": outputs,
    }


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
    db.q(
        "CREATE $r CONTENT $d",
        r=R("template", tid),
        d=store.clean(
            {
                "name": name.strip()[:80],
                "kind": kind,
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
        r=R("template_version", f"{tid}-1"),
        d=store.clean(
            {
                "template": tid,
                "version": 1,
                "body": body,
                "schema": _clean_schema(kind, schema),
                "system": system,
                "created_at": store.now(),
                "created_by": user,
            }
        ),
    )
    return tid


def rename(db, tid, name=None, description=None):
    """A template's name and description (its text changes by saving a version)."""
    if not db.one("SELECT id FROM $r", r=R("template", tid)):
        raise KeyError(tid)
    if name is not None:
        if not name.strip():
            raise TemplateProblem("give the template a name")
        db.q("UPDATE $r SET name = $n", r=R("template", tid), n=name.strip()[:80])
    if description is not None:
        db.q("UPDATE $r SET description = $d", r=R("template", tid), d=description.strip() or None)


def save_version(db, tid, body, schema=None, system=None, notes=None, user=None, publish=True):
    t = db.one("SELECT kind FROM $r", r=R("template", tid))
    if not t:
        raise KeyError(tid)
    check(body)
    n = max(db.values("SELECT VALUE version FROM template_version WHERE template = $t", t=tid) or [0]) + 1
    db.q(
        "CREATE $r CONTENT $d",
        r=R("template_version", f"{tid}-{n}"),
        d=store.clean(
            {
                "template": tid,
                "version": n,
                "body": body,
                "schema": _clean_schema(t["kind"], schema),
                "system": system,
                "notes": notes,
                "created_at": store.now(),
                "created_by": user,
            }
        ),
    )
    db.q("UPDATE $r SET updated_at = $t" + (", current = $n" if publish else ""), r=R("template", tid), t=store.now(), n=n)
    return n


def reference(v):
    """(template id, version or None) when `v` names a saved template ({"template": 5} or {"template": 5, "version":
    2}) in place of inline text, else None. TemplateProblem for a dict that doesn't."""
    if not isinstance(v, dict):
        return None
    extra = sorted(set(v) - {"template", "version"})
    tid, version = v.get("template"), v.get("version")
    if extra or not isinstance(tid, int) or isinstance(tid, bool) or tid < 1:
        raise TemplateProblem('name a saved template as {"template": <id>}, with "version" to pin one')
    if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 1):
        raise TemplateProblem("a template's version is a number from 1, or none for its current one")
    return tid, version


def get(db, tid, version=None):
    t = db.one("SELECT record::id(id) AS id, name, kind, description, current, created_at, updated_at FROM $r", r=R("template", tid))
    if not t:
        raise KeyError(tid)
    v = db.one(
        "SELECT version, body, schema, system, notes, created_at, created_by FROM $r",
        r=R("template_version", f"{tid}-{version or t['current']}"),
    )
    if not v:
        raise KeyError(f"{tid} v{version}")
    return {**t, **v}


def list_templates(db):
    counts = {}
    for v in db.rows("SELECT template FROM template_version"):
        counts[v["template"]] = counts.get(v["template"], 0) + 1
    return [
        {**t, "versions": counts.get(t["id"], 0)}
        for t in db.rows("SELECT record::id(id) AS id, name, kind, description, current, updated_at FROM template ORDER BY id")
    ]


def versions(db, tid):
    return db.rows("SELECT version, notes, created_at, created_by FROM template_version WHERE template = $t ORDER BY version DESC", t=tid)


def diff(db, tid, a, b):
    x, y = get(db, tid, a)["body"], get(db, tid, b)["body"]
    return "".join(difflib.unified_diff(x.splitlines(True), y.splitlines(True), f"version {a}", f"version {b}"))


SEEDS = [
    (
        "Meeting notes",
        "prompt",
        "TL;DR, decisions, action items and open questions.",
        'Summarise "{{ recording.title }}" ({{ recording.duration }}).\n\nSpeakers: {% for s in speakers %}{{ s.name }}{% if not loop.last %}, {% endif %}{% endfor %}\n\n'
        "Transcript:\n{{ transcript }}\n",
        {
            "type": "object",
            "required": ["tldr", "decisions", "action_items", "open_questions"],
            "properties": {
                "tldr": {"type": "string"},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "action_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["task"],
                        "properties": {"owner": {"type": "string"}, "task": {"type": "string"}, "due": {"type": "string"}},
                    },
                },
                "open_questions": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    (
        "Markdown transcript",
        "export",
        "The transcript as Markdown, one paragraph per turn.",
        "# {{ recording.title }}\n\n{{ recording.recorded_at }} · {{ recording.duration }}\n\n{% for s in segments %}**{{ s.speaker }}** ({{ s.time }}): {{ s.text }}\n\n{% endfor %}",
        None,
    ),
    (
        "One-page brief",
        "report",
        "A printable page: summary, action items, speakers and topics.",
        '<!doctype html><html><head><meta charset="utf-8"><title>{{ recording.title }}</title>\n<style>body{font:15px/1.55 Georgia,serif;max-width:46rem;margin:2rem auto;color:#1d2733}'
        "h1{font-size:1.6rem}table{border-collapse:collapse}td{padding:2px 12px 2px 0}.muted{color:#667}</style></head><body>\n"
        '<h1>{{ recording.title }}</h1><p class="muted">{{ recording.namespace }} · {{ recording.recorded_at }} · {{ recording.duration }}</p>\n'
        "{% set notes = outputs.get('meeting_notes', {}) %}{% if notes %}<h2>Summary</h2><p>{{ notes.tldr }}</p>\n"
        "{% if notes.action_items %}<h2>Action items</h2><ul>{% for a in notes.action_items %}<li>{{ a.task }}{% if a.owner %} — {{ a.owner }}{% endif %}</li>{% endfor %}</ul>{% endif %}{% endif %}\n"
        "<h2>Speakers</h2><table>{% for s in speakers %}<tr><td>{{ s.name }}</td><td>{{ (s.talk_ms or 0) | tc }}</td><td>{{ s.words }} words</td></tr>{% endfor %}</table>\n"
        '<h2>Topics</h2><p>{{ keywords[:15] | join(", ") }}</p></body></html>\n',
        None,
    ),
]


def seed(db):
    """Starter templates on a fresh archive."""
    if db.values("SELECT VALUE id FROM template LIMIT 1"):
        return
    for name, kind, desc, body, schema in SEEDS:
        create(db, name, kind, body, schema, description=desc, user="lens-archive")
