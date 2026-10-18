"""Translations made by the model (docs/api.md#translating-a-transcript): an asset's transcript in another language,
kept as a supplementary file with the role `translation` and that language, exactly like one uploaded, so search, the
Files tab, IIIF and exports read it as they read any translation.

It runs only when someone asks (the API, MCP, or the assistant once the person approves), as a job step
(`translate`), so it runs in the background, waits on a budget that would go over (jobs._budget) and counts every
model call in the activity ledger. The transcript's lines go to the configured model in batches, numbered, marked as
data (untrusted.py) so instructions inside a transcript are translated, never followed; each translated line keeps
its original line's times and speaker. Before each batch the budgets the job counts for are looked at again, so a
long transcript stops when one is used up rather than running past it. A line the model doesn't give back is asked for
once more, then kept as it was and counted in the file's description.

Refine later: resume a translation stopped part-way instead of starting again; a `translate` pipeline step; choosing
the model per translation; translating again replacing the earlier file into the same language; captions and other
supplementary files as the source; a glossary of names and terms to keep."""

from __future__ import annotations

import json
import math
import re

from . import activity, budgets, files, llm, render, store, telemetry, untrusted

R = store.R
STEP = "translate"
BATCH_LINES = 40  # lines in one call
BATCH_CHARS = 6000  # characters of them in one call (one longer line goes alone)
PROMPT_TOKENS = 350  # what each call's instructions and markers take, for estimates
OUT_RATIO = 1.3  # tokens out per token in: a translation can take more tokens than its source
GROWTH = 4  # a translated line longer than this many times its source (plus GROWTH_SLACK) is cut there
GROWTH_SLACK = 200
SYSTEM = (
    "You translate transcripts. Translate each numbered line into the language asked for, keeping its meaning, tone "
    "and register, and keeping names, numbers and technical terms as a careful translator would. Translate every line "
    "on its own and in order, under its own number: never merge, split, skip, summarise, explain or add lines. Reply "
    "with JSON only."
)
SCHEMA = {
    "type": "object",
    "required": ["translations"],
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "text"],
                "properties": {"n": {"type": "integer"}, "text": {"type": "string"}},
            },
        }
    },
}


class Problem(ValueError):
    """Why a translation can't be made now (no model, no text, a language that isn't one)."""


class OverBudget(Problem):
    """A budget the translation counts for is used up."""


def language(v):
    """A language code to translate into (en, pt-BR); Problem when it isn't one."""
    try:
        code = files._language(v)
    except ValueError as e:
        raise Problem(str(e)) from None
    if not code:
        raise Problem("Choose the language to translate into, such as fr or pt-BR.")
    return code


def _segments(db, rid):
    return db.rows("SELECT idx, t0, t1, speaker, text, page FROM segment WHERE recording = $r ORDER BY idx", r=int(rid))


def _clean(text):
    return " ".join(str(text or "").split())


def batches(segs):
    """The transcript's lines in groups to send at once: up to BATCH_LINES lines and BATCH_CHARS characters each."""
    out, cur, size = [], [], 0
    for s in segs:
        n = len(_clean(s.get("text")))
        if cur and (len(cur) >= BATCH_LINES or size + n > BATCH_CHARS):
            out.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += n
    return out + ([cur] if cur else [])


def _kind(rec):
    return (rec.get("media") or {}).get("kind") or ("audio" if rec.get("source") == "audio" else rec.get("source") or "transcript")


def estimate(db, cfg, rid):
    """What translating the asset's transcript will likely take, always labelled an estimate: its characters as
    tokens (about four to a token, more coming out than going in), in BATCH_LINES-line calls, at the model's price in
    Settings. The shape of a batch run's estimate (batches.estimate), with the lines and how it was worked out."""
    rec = db.one("SELECT duration_ms, media, source FROM $r", r=R("recording", int(rid))) or {}
    segs = _segments(db, rid)
    chars = sum(len(_clean(s.get("text"))) for s in segs)
    calls = len(batches(segs))
    tin = math.ceil(chars / 4) + calls * PROMPT_TOKENS
    tout = math.ceil(chars / 4 * OUT_RATIO) + calls * 10
    model = cfg["llm"].get("model")
    cost = telemetry.cost(model, tin, tout, telemetry.prices(cfg)) if model and calls else None
    priced = f"at {model}'s price in Settings" if cost is not None else "with no price for the model in Settings"
    return {
        "recordings": 1,
        "by_kind": {_kind(rec): 1},
        "hours": round((rec.get("duration_ms") or 0) / 3.6e6, 2),
        "seconds": int(tout / 40),  # ~40 tokens a second from a local model
        "llm": {
            "calls": calls,
            "input_tokens": tin,
            "output_tokens": tout,
            "cost": round(cost, 4) if cost is not None else None,
            "model": model,
        },
        "would_replace": 0,
        "needs_confirmation": False,
        "confirm_text": None,
        "lines": len(segs),
        "basis": f"{len(segs):,} lines, {chars:,} characters, about four to a token, in {calls} call{'s' if calls != 1 else ''} {priced}",
        "estimate": True,
    }


def _refs(db, rid):
    rec = db.one("SELECT space FROM $r", r=R("recording", int(rid))) or {}
    return [f"recording:{int(rid)}", f"space:{rec['space']}" if rec.get("space") is not None else None]


def translating(db, rid, code):
    """The job already translating the asset into this language, if there is one."""
    from . import jobs

    for j in db.rows(
        "SELECT record::id(id) AS id, steps, step_index FROM job WHERE recording = $r AND status IN $a", r=int(rid), a=jobs.ACTIVE
    ):
        steps = j.get("steps") or []
        if any(isinstance(s, dict) and s.get("type") == STEP and s.get("language") == code for s in steps[j.get("step_index") or 0 :]):
            return j["id"]
    return None


def problem(db, cfg, rid):
    """Why the asset can't be translated now, or None: no model, no text and none on the way, or a budget it counts for
    used up."""
    from . import jobs

    if not llm.configured(cfg):
        return "No language model is configured (Settings → LLM provider), so Lens can't translate."
    has_text = db.values("SELECT VALUE id FROM segment WHERE recording = $r LIMIT 1", r=int(rid))
    busy = db.values("SELECT VALUE id FROM job WHERE recording = $r AND status IN $a LIMIT 1", r=int(rid), a=jobs.ACTIVE)
    if not has_text and not busy:
        return "This asset has no transcript to translate yet."
    why = budgets.used_up(db, _refs(db, rid))
    return f"Over budget: {why}" if why else None


def queue(db, cfg, rid, code, by=None, by_id=None):
    """Translate the asset's transcript into `code` in the background: a `translate` step after what its job still has
    to do (jobs.add_steps), or a new job; the job already translating it into that language if there is one. Problem
    (OverBudget for a budget) when it can't; KeyError for no such asset."""
    from . import jobs

    code = language(code)
    rec = db.one("SELECT language FROM $r", r=R("recording", int(rid)))
    if rec is None:
        raise KeyError(rid)
    why = problem(db, cfg, rid)
    if why:
        raise (OverBudget if why.startswith("Over budget") else Problem)(why)
    if str(rec.get("language") or "").lower() == code.lower():
        raise Problem(f"The transcript is already in {code}.")
    jid = translating(db, rid, code)
    if jid is not None:
        return jid
    return jobs.add_steps(db, int(rid), [store.clean({"type": STEP, "language": code, "by": by_id})], by=by)


def _prompt(code, group, rid, cfg):
    numbered = "\n".join(f"[{i}] {_clean(s.get('text'))}" for i, s in enumerate(group, 1))
    return (
        f'Translate these {len(group)} lines into the language whose BCP 47 code is "{code}". Reply with '
        '{"translations": [{"n": <the line\'s number>, "text": <its translation>}, ...]}, one item for each line, '
        "translating what each line says and nothing else.\n\n" + untrusted.wrap(numbered, f"transcript of recording {rid}", cfg)
    )


def _ask(cfg, code, group, rid, model, keep):
    """{line number in the group: its translation} for the lines the model gave back."""
    value = llm.json_out(cfg, SYSTEM + untrusted.rules(cfg), _prompt(code, group, rid, cfg), SCHEMA, model)
    got = {}
    for item in value.get("translations") or []:
        n, text = item.get("n"), _clean(item.get("text"))
        if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(group) and text and n not in got:
            most = GROWTH * len(_clean(group[n - 1].get("text"))) + GROWTH_SLACK
            got[n] = untrusted.defang(text[:most], keep)
    return got


def _over(db):
    why = budgets.used_up(db, activity.current())
    if why:
        raise OverBudget(f"over budget, stopped before translating the rest: {why}")


def _name(title, code):
    t = re.sub(r"[\\/]+", "-", _clean(title) or "Transcript")
    return files.clean_name(f"{t[:150]} ({code}).json")


def translate(db, cfg, rid, say, spec):
    """The `translate` step: the transcript's lines through the model in batches, then kept as a translation file in
    Lens's own transcript JSON (lens/1), each line with its original times and speaker. Returns the new file."""
    from . import jobs

    code = language((spec or {}).get("language"))
    rec = db.one("SELECT title FROM $r", r=R("recording", int(rid)))
    if rec is None:
        raise KeyError(rid)
    segs = _segments(db, rid)
    if not segs:
        raise jobs.Skip("there's no transcript to translate")
    model = (spec or {}).get("model") or cfg["llm"]["model"]
    keep = untrusted.links("\n".join(s.get("text") or "" for s in segs))
    groups = batches(segs)
    said, kept, calls = [], 0, 0
    say(f"translating {len(segs)} lines into {code} with {model}, in {len(groups)} batch(es)")
    for k, group in enumerate(groups, 1):
        _over(db)
        got = _ask(cfg, code, group, rid, model, keep)
        calls += 1
        missing = [i for i in range(1, len(group) + 1) if i not in got]
        if missing:  # asked once more, on their own
            _over(db)
            again = _ask(cfg, code, [group[i - 1] for i in missing], rid, model, keep)
            calls += 1
            got.update({missing[j - 1]: t for j, t in again.items()})
        for i, s in enumerate(group, 1):
            if i not in got:
                kept += 1
            said.append(got.get(i) or _clean(s.get("text")))
        say(f"batch {k} of {len(groups)}: {len(said)} of {len(segs)} lines translated")
    timed = all(s.get("t0") is not None and s.get("t1") is not None and s.get("page") is None for s in segs)
    names = render.speaker_names(db, [s.get("speaker") for s in segs])
    doc = {
        "lens": "lens/1",
        "doc": {"title": rec.get("title"), "speakers": {}, "language": code},
        "segments": [
            {
                "t0": s["t0"] if timed else None,
                "t1": s["t1"] if timed else None,
                "speaker": names.get(s.get("speaker")),
                "text": text,
            }
            for s, text in zip(segs, said, strict=True)
        ],
    }
    path = files.incoming(cfg)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    description = (
        f"Translated into {code} from the transcript by the model ({model}): {len(segs):,} lines in {calls} call{'s' if calls != 1 else ''}."
        + (f" {kept:,} of them it didn't give back, kept as they were." if kept else "")
    )
    f = files.add(db, cfg, rid, path, _name(rec.get("title"), code), "translation", code, None, description, (spec or {}).get("by"))
    say(f"saved the translation as file {f['id']} ({f['name']})" + (f"; {kept} lines kept untranslated" if kept else ""))
    return f


def status(db, rid, code=None):
    """The asset's translation files, uploaded or made (in the order the Files tab lists them), and the latest job that
    was to translate it (into `code`, when given): its id, status and, when it failed, why."""
    made = [
        store.clean({k: f.get(k) for k in ("id", "name", "language", "label", "description", "lines", "timed", "created_at")})
        for f in files.of(db, rid)
        if f.get("role") == "translation"
    ]
    rows = db.rows("SELECT record::id(id) AS id, status, error, steps FROM job WHERE recording = $r ORDER BY id DESC LIMIT 20", r=int(rid))
    job = next(
        (
            j
            for j in rows
            if any(isinstance(s, dict) and s.get("type") == STEP and code in (None, s.get("language")) for s in j.get("steps") or [])
        ),
        None,
    )
    out = {"id": job["id"], "status": job["status"], "error": job.get("error") if job["status"] == "failed" else None} if job else None
    return {"translations": made, "job": store.clean(out) if out else None}
