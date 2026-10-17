"""Specs from transcripts (docs/notes.md#specs-from-transcripts): what someone said in a voice session, a meeting or a
memo, written up as a technical spec in a note linked to the recording. The note keeps every named decision, every no
and every "not that, this", and every constraint the speaker stated, each in their own words with the moment they said
it, and flags each gap they left to decide later.

The prompt is an ordinary prompt template (templates.py), "Spec from transcript", made from the built-in text when Lens
starts and found again by its role, so it is edited, versioned and previewed on the Templates page like any other.
Writing a spec is a job step (`spec`), so it runs in the background, counts against budgets and goes through the
activity ledger like every model call; a pipeline can carry it too. The transcript reaches the model marked as data
(untrusted.py). Each quote is looked for in the transcript: one that isn't there word for word is marked as not found,
not passed off as the speaker's words, and a transcript longer than the model is given says where it was cut. Writing
the spec again updates the same note, whose history keeps what it said before.

Refine later: transcripts longer than llm.max_chars read in parts, not cut; specs from a chat conversation; a spec
from several recordings at once."""

from __future__ import annotations

import bisect
import re

from . import llm, notebook, pipelines, store, templates, untrusted

R = store.R
ROLE = "spec.transcript"
KEY = "spec"  # the output a recording keeps its spec's note under (pipelines.save_output)
NAME = "Spec from transcript"
DESCRIPTION = (
    "A technical spec from a voice session: every decision, every no and every constraint in the speaker's words, and "
    "the gaps left to decide later."
)
# the owner's own words: the prompt as he wrote it, kept verbatim as the template's opening
PROMPT = (
    "You are writing a technical spec from a voice-session transcript. Preserve every named decision, every 'no' and "
    "every 'not that — this', and every constraint the speaker stated. Do not paraphrase their reasoning. Flag any gap "
    "where the speaker said they would decide later."
)
BODY = (
    PROMPT + "\n\n"
    "Fill in:\n"
    "- title: what the spec is for, in a few words.\n"
    "- summary: one line on what it specifies.\n"
    "- spec: the technical spec in Markdown, in ## sections that fit what was said (goal, scope, design, requirements "
    "and the like), with the speaker's reasoning in their own words.\n"
    "- decisions: each named decision, with the speaker's words for it and for their reasoning (quote) and when they "
    "said it (time).\n"
    "- rejected: each 'no' and each 'not that — this': what was turned down (not), what they chose instead (instead, "
    "when they said), the quote and the time.\n"
    "- constraints: each constraint the speaker stated, with the quote and the time.\n"
    "- gaps: each point the speaker said they would decide later, with the quote and the time.\n"
    "Copy every quote word for word from the transcript, and give its time as the transcript writes it, like 12:03. "
    "Leave a list empty when the transcript has none of it: never invent one.\n\n"
    'Session: "{{ recording.title }}" ({{ recording.duration }}{% if recording.recorded_at %}, recorded '
    "{{ recording.recorded_at }}{% endif %})\n"
    "{% if speakers %}Speakers: {% for s in speakers %}{{ s.name or 'Unknown' }}{% if not loop.last %}, {% endif %}"
    "{% endfor %}\n{% endif %}\n"
    "Transcript:\n{{ transcript }}\n"
)
SYSTEM = (
    "You write technical specs from transcripts of what people said. Keep the speaker's own words: copy quotes exactly "
    "as the transcript has them, and never invent a decision, a constraint or a quote. Reply with JSON only."
)
_QUOTED = {"quote": {"type": "string"}, "time": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "required": ["title", "spec", "decisions", "rejected", "constraints", "gaps"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "spec": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {"type": "object", "required": ["decision", "quote"], "properties": {"decision": {"type": "string"}, **_QUOTED}},
        },
        "rejected": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["not", "quote"],
                "properties": {"not": {"type": "string"}, "instead": {"type": "string"}, **_QUOTED},
            },
        },
        "constraints": {
            "type": "array",
            "items": {"type": "object", "required": ["constraint", "quote"], "properties": {"constraint": {"type": "string"}, **_QUOTED}},
        },
        "gaps": {
            "type": "array",
            "items": {"type": "object", "required": ["gap", "quote"], "properties": {"gap": {"type": "string"}, **_QUOTED}},
        },
    },
}
# each list of the model's answer: the field naming the item, its heading in the note, and what it says when empty
SECTIONS = (
    ("decisions", "decision", "Decisions", "No named decisions in the transcript."),
    ("rejected", "not", "No, and not that — this", "Nothing turned down in the transcript."),
    ("constraints", "constraint", "Constraints", "No constraints stated in the transcript."),
    ("gaps", "gap", "Decide later", "Nothing left to decide later."),
)
ITEMS_MAX = 200  # per list
LINE_MAX = 1000  # characters of a decision, a constraint or a gap
QUOTE_MAX = 2000
SPEC_MAX = 150_000  # characters of the spec's own text, leaving room in the note for the lists


class Problem(ValueError):
    pass


def template(db):
    """The prompt template specs are written with: the one whose role is spec.transcript, made from the built-in text the
    first time (when Lens starts, so it can be edited before the first spec). Safe to call again: it finds the one there
    is."""
    row = db.one("SELECT record::id(id) AS id FROM template WHERE role = $r ORDER BY id LIMIT 1", r=ROLE)
    if row:
        return templates.get(db, row["id"])
    tid = templates.create(db, NAME, "prompt", BODY, SCHEMA, system=SYSTEM, description=DESCRIPTION, user="lens-specs")
    db.q("UPDATE $t SET role = $r", t=R("template", tid), r=ROLE)
    return templates.get(db, tid)


def _chosen(db, spec):
    """The template a step names ({"template", "version"}), which must be a prompt, else the built-in one."""
    if not (spec or {}).get("template"):
        return template(db)
    try:
        t = templates.get(db, int(spec["template"]), spec.get("version"))
    except (KeyError, TypeError, ValueError):
        raise Problem("choose a prompt template to write the spec with") from None
    if t["kind"] != "prompt":
        raise Problem(f"a spec is written with a prompt template, not a {t['kind']} template")
    return t


def queue(db, cfg, rid, by=None, template_id=None, version=None):
    """Write a recording's spec in the background: a `spec` step after what its job still has to do (jobs.add_steps), or
    a new job. Problem when no model is set up, the template isn't a prompt, or there's no text and none on the way."""
    from . import jobs

    if not llm.configured(cfg):
        raise Problem("no language model is configured (Settings → LLM provider)")
    step = {"type": "spec"}
    if template_id is not None:
        t = _chosen(db, {"template": template_id, "version": version})
        step.update(template=t["id"], **({"version": version} if version else {}))
    if not db.one("SELECT id FROM $r", r=R("recording", rid)):
        raise KeyError(rid)
    has_text = db.values("SELECT VALUE id FROM segment WHERE recording = $r LIMIT 1", r=rid)
    busy = db.values("SELECT VALUE id FROM job WHERE recording = $r AND status IN $a LIMIT 1", r=rid, a=jobs.ACTIVE)
    if not has_text and not busy:
        raise Problem("this asset has no text to write a spec from")
    return jobs.add_steps(db, rid, [step], by=by)


# ---------- finding quotes in the transcript ----------
def _words(text):
    return re.findall(r"\w+", re.sub(r"['’`]", "", str(text or "").lower()))


class Transcript:
    """A recording's lines as one run of words, to find a quote in word for word (case, punctuation and line breaks
    aside) and say where it starts."""

    def __init__(self, segments):
        self.segments = segments
        words, self.starts, self.owner = [], [], []
        pos = 1
        for i, s in enumerate(segments):
            for w in _words(s.get("text")):
                words.append(w)
                self.starts.append(pos)
                self.owner.append(i)
                pos += len(w) + 1
        self.text = " " + " ".join(words) + " "

    def find(self, quote):
        """The line a quote starts in, when the transcript holds it word for word (an ellipsis may skip words between
        its parts), else None."""
        parts = [p for p in (_words(x) for x in re.split(r"\.\.\.|…", str(quote or ""))) if p]
        if not parts:
            return None
        at, first = 0, None
        for words in parts:
            hit = self.text.find(" " + " ".join(words) + " ", at)
            if hit < 0:
                return None
            if first is None:
                first = hit
            at = hit + len(" ".join(words)) + 1
        return self.segments[self.owner[bisect.bisect_left(self.starts, first + 1)]]


# ---------- the note ----------
def _line(v, n=LINE_MAX):
    return " ".join(str(v or "").split())[:n]


def _moment(rid, seg):
    """Where a line is, as a link the web app opens there: its page in a document, its time in a recording."""
    if seg.get("page") is not None:
        return f"[p. {seg['page'] + 1}](/resources/{rid}?page={seg['page'] + 1})"
    t0 = seg.get("t0") or 0
    return f"[{store.tc(t0)}](/resources/{rid}" + (f"?t={int(t0) // 1000})" if t0 >= 1000 else ")")


def _items(value, key, field):
    """A list from the model's answer, each item {field, quote, time, instead?}; plain strings count as the field."""
    out = []
    for x in (value.get(key) or [])[:ITEMS_MAX]:
        if isinstance(x, str):
            x = {field: x}
        if isinstance(x, dict) and _line(x.get(field)):
            out.append(x)
    return out


def compose(rid, rec_title, value, segments, safe, cut_at=None, template_name=NAME, version=None):
    """The spec's note: (title, summary, Markdown body, counts). `safe` makes the model's text safe to keep (links it
    built broken up); `cut_at` is the line where the transcript the model read was cut, if it was."""
    tr = Transcript(segments)
    counts = {key: 0 for key, *_ in SECTIONS} | {"not_found": 0}
    lists = []
    for key, field, heading, empty in SECTIONS:
        lines = []
        for x in _items(value, key, field):
            text = safe(_line(x.get(field))).rstrip(".")
            quote = _line(x.get("quote"), QUOTE_MAX).strip('"“”')
            seg = tr.find(quote) if quote else None
            if key == "rejected":
                instead = safe(_line(x.get("instead"))).rstrip(".")
                said = f"**Not:** {text}." + (f" **Instead:** {instead}." if instead else "")
            else:
                said = f"**{text}.**"
            if quote and seg is not None:
                said += f' "{safe(quote)}" ({_moment(rid, seg)})'
            elif quote:  # the model's words, not the speaker's: no moment to link to, and it says so
                counts["not_found"] += 1
                said += f' "{safe(quote)}" *(not found word for word in the transcript)*'
            lines.append(("- [ ] " if key == "gaps" else "- ") + said)
        counts[key] = len(lines)
        lists.append(f"## {heading}\n\n" + ("\n".join(lines) if lines else empty))
    title = _line(safe(_line(value.get("title"))), 180) or _line(rec_title, 180) or "Untitled"
    label = _line(rec_title, 180).replace("[", "(").replace("]", ")") or "the recording"
    head = [
        f"From @[{label}](recording:{rid}), written by the model with the "
        f'"{template_name}" template{f" (version {version})" if version else ""}. Quotes are the speaker\'s words, '
        "each linked to when they said it."
    ]
    if counts["gaps"]:
        n = counts["gaps"]
        head.append(f"> **Left to decide:** the speaker left {n} thing{'s' if n != 1 else ''} to decide later, under Decide later.")
    if cut_at is not None:
        head.append(
            f"> **Not all of it was read:** the transcript is longer than the model is given (Settings → LLM provider, "
            f"llm.max_chars), so nothing said after {_moment(rid, cut_at)} is in this spec."
        )
    if counts["not_found"]:
        n = counts["not_found"]
        head.append(f"> **Check:** {'1 quote isn’t' if n == 1 else f'{n} quotes aren’t'} in the transcript word for word; each says so.")
    spec = safe(str(value.get("spec") or "").strip())[:SPEC_MAX]
    spec = re.sub(r"^# ", "## ", spec, flags=re.M)  # the note's title is its only top heading
    body = "\n\n".join(head + ([spec] if spec else []) + lists) + "\n"
    summary = _line(safe(_line(value.get("summary"))), notebook.SUMMARY_MAX) or (
        f"{counts['decisions']} decisions, {counts['rejected']} turned down, {counts['constraints']} constraints, "
        f"{counts['gaps']} left to decide"
    )
    return f"Spec: {title}"[: notebook.TITLE_MAX], summary, body[: notebook.BODY_MAX], counts


def _context(db, cfg, rid):
    """The template's context (templates.context) with the transcript marked as data, and the number of the last line
    the model reads whole when the transcript is longer than llm.max_chars (else None)."""
    ctx = templates.context(db, cfg, rid)
    cut, cap = None, cfg["llm"].get("max_chars") or 24000
    if len(ctx["full_transcript"]) > cap:  # cut there, as templates.context does
        used = -1
        for i, s in enumerate(ctx["segments"]):
            used += len(f"[{s['time']}] {s['speaker']}: {s['text']}") + 1
            if used > cap:
                cut = max(i - 1, 0)
                break
    source = f"transcript of recording {rid}"
    ctx["transcript"] = untrusted.mark(ctx["transcript"], source, cfg)
    ctx["full_transcript"] = untrusted.mark(ctx["full_transcript"], source, cfg)
    for s in ctx["segments"]:
        s["text"] = untrusted.mark(s["text"], source, cfg)
    return ctx, cut


def write(db, cfg, rid, say, spec=None):
    """The `spec` step: render the template against the recording, ask the model, check the quotes, and write the note
    (or update the one written before, if it's still there and in the recording's namespace). Saves output `spec`."""
    spec = spec or {}
    t = _chosen(db, spec)
    rec = db.one("SELECT space, title, recorded_at FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    segments = db.rows("SELECT idx, t0, page, text FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    ctx, cut = _context(db, cfg, rid)
    prompt = templates.render_body(t["body"], ctx, templates.PARAM)
    model = spec.get("model") or cfg["llm"]["model"]
    say(f"asking {model} for the spec ({len(prompt)} characters)")
    value = llm.json_out(cfg, (t.get("system") or SYSTEM) + untrusted.rules(cfg), prompt, t.get("schema") or SCHEMA, spec.get("model"))
    keep = untrusted.links("\n".join(s.get("text") or "" for s in segments))
    title, summary, body, counts = compose(
        rid,
        rec.get("title"),
        value,
        segments,
        lambda text: untrusted.defang(text, keep),
        segments[cut] if cut is not None and cut < len(segments) else None,
        t["name"],
        t["version"],
    )
    before = ((db.one("SELECT * FROM $o", o=R("output", f"{rid}-{KEY}")) or {}).get("value") or {}).get("note")  # `value` is a keyword
    page = db.one("SELECT space FROM $r", r=R("note_page", int(before))) if isinstance(before, int) else None
    if page and page["space"] == rec["space"]:
        pid = before
        notebook.update(db, pid, None, title=title, body=body, summary=summary, author="assistant", why="the spec was written again")
    else:
        day = str(rec.get("recorded_at") or "")[:10]  # the day it's about: the day it was said
        day = day if re.match(r"\d{4}-\d{2}-\d{2}$", day) else None
        pid = notebook.create(db, rec["space"], None, title, body, summary, date=day, author="assistant")
    db.q("UPDATE $r SET refine_pending = false", r=R("note_page", pid))  # the model has just written its title and summary
    pipelines.save_output(
        db,
        rid,
        KEY,
        {"note": pid, "title": title, **counts, "cut": cut is not None},
        {"template": t["id"], "version": t["version"], "model": model},
    )
    say(
        f"wrote the spec to note {pid}: {counts['decisions']} decisions, {counts['rejected']} turned down, "
        f"{counts['constraints']} constraints, {counts['gaps']} left to decide"
        + (f"; {counts['not_found']} quotes not found in the transcript" if counts["not_found"] else "")
    )
    return pid


def status(db, rid):
    """What there is of a recording's spec: `writing` while a job is on it, `failed` when the last job that was to write
    it failed (with why; a spec written before that is still there), `ready` with its note, else `none`; with the counts
    of what the note holds and the template it was written with (else the one it would be)."""
    from . import jobs

    row = db.one("SELECT * FROM $o", o=R("output", f"{rid}-{KEY}")) or {}  # `value` is a keyword
    v, origin = row.get("value") or {}, row.get("origin") or {}
    note = v.get("note") if isinstance(v.get("note"), int) and db.one("SELECT id FROM $r", r=R("note_page", v["note"])) else None
    job = next(
        (
            j
            for j in db.rows(
                "SELECT record::id(id) AS id, status, error, steps FROM job WHERE recording = $r ORDER BY id DESC LIMIT 20", r=rid
            )
            if any((s or {}).get("type") == "spec" for s in j.get("steps") or [])
        ),
        None,
    )
    state = "writing" if job and job["status"] in jobs.ACTIVE else "failed" if job and job["status"] == "failed" else None
    tid = origin.get("template") or template(db)["id"]
    return store.clean(
        {
            "status": state or ("ready" if note else "none"),
            "job": job["id"] if job else None,
            "error": job.get("error") if state == "failed" else None,
            "template": tid,
            **(
                {
                    "note": note,
                    "title": v.get("title"),
                    "written_at": row.get("created_at"),
                    "version": origin.get("version"),
                    "model": origin.get("model"),
                    "cut": v.get("cut"),
                    **{k: v.get(k, 0) for k in [key for key, *_ in SECTIONS] + ["not_found"]},
                }
                if note
                else {}
            ),
        }
    )
