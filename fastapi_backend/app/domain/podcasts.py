"""Podcasts: two-host learning episodes made from what someone picked, with every claim cited to a real source.

An episode is a recording. Its script lines are the recording's segments (one line, one segment, spoken by the host
who says it), so search, entities, the graph, the player and the transcript view work on it as on anything else. What
only an episode has lives beside it in `podcast:<recording id>`: what was asked for, the numbered excerpts the model
saw (`sources`), the outline, the lines with their citations, and what the fact-check changed (`checks`).

It's made by one job on that recording, one step per stage (all of type "podcast", told apart by `stage`):

    gather    the picked recordings and lines become numbered excerpts, within podcasts.context_chars
    plan      an outline: key ideas, the questions a learner would ask, connections between sources, a recap
    write     the script as {speaker, kind, text, citations}; citations to excerpts it wasn't given are dropped
    check     a second pass checks every claim against the excerpts it cites; an unsupported claim is rewritten
              once (and checked again) or dropped, and every change is recorded
    publish   the lines become the recording's transcript, timed at a speaking pace

then the usual analyze, embed and summarize steps. The prompts are prompt templates (templates.py) that anyone who
edits templates can change; each stage uses the newest version of the template whose `role` is podcast.<stage>.
Citations point at runs of segments ({recording, idx0, idx1}), which exist with or without search by meaning.
"""

from __future__ import annotations

import hashlib
import pathlib

from . import chat, jobs, llm, render, store, templates

R = store.R
STAGES = ("gather", "plan", "write", "check", "audio", "publish")
STAGE_NAMES = {
    "gather": "gathering",
    "plan": "planning",
    "write": "writing",
    "check": "fact-checking",
    "audio": "recording the voices",
    "publish": "publishing",
}
AFTER = ["analyze", "embed", "summarize"]  # what any new transcript gets
STATUS = {"gather": "gathering", "plan": "planning", "write": "writing", "check": "checking", "audio": "rendering", "publish": "publishing"}
# the second host's voice when podcasts.voice_b isn't set, per text-to-speech provider (the first's is voice.tts_voice)
SECOND_VOICE = {"openai": "onyx", "elevenlabs": "EXAVITQu4vr4xnSDxMKL", "deepgram": "aura-2-orion-en"}
PAUSE_MS = {"turn": 450, "same": 250}  # silence before a line: when the other host speaks, and when the same one goes on
LENGTHS = (5, 10, 20)  # minutes offered; anything from 1 to podcasts.max_minutes goes
STYLES = {
    "deep-dive": "A deep dive: build understanding step by step, with concrete examples and one or two analogies.",
    "recap": "A quick recap: the essentials only, brisk, no tangents.",
    "debate": "Compare and contrast: set the sources side by side, where they agree, where they differ, and why.",
    "beginner": "For a beginner: no jargon without a plain explanation, more analogies, check understanding often.",
}
KINDS = ("claim", "question", "analogy", "banter", "recap")
CITED = ("claim", "recap")  # lines that state facts, so need a source
RUN_CHARS = 700  # an excerpt is a run of neighbouring lines up to about this long (a document's stays on one page)
LINE_CHARS = 600  # longest script line kept


class Problem(ValueError):
    pass


# ---------- what was picked ----------
def clean_selection(sel):
    """{recordings: [ids], excerpts: [{recording, idx}]}, deduplicated; ValueError when nothing is picked."""
    sel = sel or {}
    recs = list(dict.fromkeys(int(r) for r in sel.get("recordings") or []))
    seen, excerpts = set(recs), []
    for x in sel.get("excerpts") or []:
        k = (int(x["recording"]), int(x["idx"]))
        if k[0] not in seen and k not in {(e["recording"], e["idx"]) for e in excerpts}:
            excerpts.append({"recording": k[0], "idx": k[1]})
    if not recs and not excerpts:
        raise Problem("pick at least one resource or passage")
    return {"recordings": recs, "excerpts": excerpts}


def selected_recordings(sel):
    return sorted(set(sel.get("recordings") or []) | {x["recording"] for x in sel.get("excerpts") or []})


# ---------- options ----------
def options(cfg, length=None, style=None, prompt=None, voices=None):
    p = cfg.get("podcasts") or {}
    length = 10 if length is None else int(length)
    if not 1 <= length <= int(p.get("max_minutes") or 30):
        raise Problem(f"length is 1 to {p.get('max_minutes') or 30} minutes")
    style = style or "deep-dive"
    if style not in STYLES:
        raise Problem(f"style is one of {', '.join(STYLES)}")
    prompt = (prompt or "").strip()
    if len(prompt) > 2000:
        raise Problem("keep the prompt under 2000 characters")
    picked = {}
    for k, v in (voices or {}).items():
        if k not in ("a", "b") or not isinstance(v, str) or len(v.strip()) > 100:
            raise Problem("voices are {a, b}: a voice's name or id for each host")
        if v.strip():
            picked[k] = v.strip()
    return store.clean({"length": length, "style": style, "prompt": prompt or None, "voices": picked or None})


def hosts(cfg):
    p = cfg.get("podcasts") or {}
    return {"a": p.get("host_a") or "Alex", "b": p.get("host_b") or "Sam"}


# ---------- where an episode goes ----------
def _readers(db, sid, collections=True):
    """Who besides admins can read namespace `sid`: its members and, with `collections`, people given a role on one of
    its collections."""
    out = set(db.values("SELECT VALUE account FROM membership WHERE space = $s", s=sid))
    if collections:
        cids = db.values("SELECT VALUE record::id(id) FROM collection WHERE space = $s", s=sid)
        if cids:
            out |= set(db.values("SELECT VALUE account FROM collection_role WHERE collection IN $c", c=cids))
    return out


def _vault(db, sid):
    return bool((db.one("SELECT vault FROM $r", r=R("data_key", int(sid))) or {}).get("vault"))


def target(db, cfg, source_spaces, roles, admin):
    """(namespace id, why) for a new episode. The podcasts namespace (podcasts.namespace) when the person may add to
    it and everyone who can read it can read every source's namespace, and no source is in a vault; else the source's
    own namespace when all sources share one the person may add to. Raises Problem when neither will do."""
    from . import auth

    name = (cfg.get("podcasts") or {}).get("namespace") or "podcasts"
    row = db.one("SELECT record::id(id) AS id FROM space WHERE name = $n LIMIT 1", n=name)
    sid = row["id"] if row else None
    can_add = admin or (sid is not None and auth.allows(roles, sid, "editor"))
    if can_add and not any(_vault(db, s) for s in source_spaces):
        readers = _readers(db, sid) if sid is not None else set()
        if all(readers <= _readers(db, s, collections=False) for s in source_spaces if s != sid):
            return (sid if sid is not None else store.ns_id(db, name)), "podcasts"
    if len(source_spaces) == 1:
        (only,) = source_spaces
        if admin or auth.allows(roles, only, "editor"):
            return only, "sources"
    raise Problem(
        f"an episode about these sources can't go in {name} (people there couldn't read them all), and they're in "
        "more than one namespace or one you can't add to: pick sources from one namespace you edit"
    )


# ---------- starting ----------
def create(
    db,
    cfg,
    selection,
    by=None,
    roles=None,
    admin=False,
    readable=None,
    also=(),
    prompt=None,
    length=None,
    style=None,
    title=None,
    voices=None,
):
    """A new episode about what was picked, queued to be made. Returns {episode, job, namespace, placed}.

    `roles` ({space: role}) and `admin` decide where it may go; `readable` (None: everything) and `also` (recordings
    seen through a role on their collection) bound the sources it may read, and are kept for the job. Callers check the person may read every picked recording first."""
    sel = clean_selection(selection)
    opts = options(cfg, length, style, prompt, voices)
    rids = selected_recordings(sel)
    rows = db.rows("SELECT record::id(id) AS id, space, title FROM recording WHERE id IN $ids", ids=[R("recording", r) for r in rids])
    if len(rows) != len(rids):
        raise KeyError(sorted(set(rids) - {r["id"] for r in rows})[0])
    also = sorted({int(x) for x in also or ()})
    if readable is not None and any(r["space"] not in readable and r["id"] not in also for r in rows):
        raise KeyError("recording")
    sid, placed = target(db, cfg, {r["space"] for r in rows}, roles or {}, admin)
    rid = db.next_id("recording")
    t = store.now()
    first = next(r["title"] for r in rows if r["id"] == rids[0]) or "Untitled"
    db.q(
        "CREATE $r CONTENT $d",
        r=R("recording", rid),
        d=store.clean(
            {
                "space": sid,
                "collection": _collection(db, sid),
                "fingerprint": f"podcast-{rid}",
                "fp_key": f"{sid}:podcast-{rid}",
                "path": f"podcast:{rid}",
                "source": "transcript",
                "engine": "podcast",
                "title": (title or "").strip()[:200] or f"Podcast: {first}"[:200],
                "recorded_at": t,
                "status": "new",
                "created_at": t,
                "created_by": by,
            }
        ),
    )
    db.q(
        "CREATE $r CONTENT $d",
        r=R("podcast", rid),
        d=store.clean(
            {
                "recording": rid,
                "space": sid,
                "status": "queued",
                "request": {"selection": sel, **opts, "hosts": hosts(cfg)},
                "readable": sorted(readable) if readable is not None else None,
                "also": also or None,
                "by": by,
                "created_at": t,
                "updated_at": t,
            }
        ),
    )
    jid = jobs.enqueue(db, rid, steps(), by=by)
    return {"episode": rid, "job": jid, "namespace": sid, "placed": placed}


def steps():
    return [{"type": "podcast", "stage": s, "name": STAGE_NAMES[s]} for s in STAGES] + AFTER


def _collection(db, sid):
    """The namespace's "Podcasts" collection (made when missing)."""
    from . import hierarchy

    key = store.collection_key(sid, None, "Podcasts")
    row = db.one("SELECT record::id(id) AS id FROM collection WHERE key = $k LIMIT 1", k=key)
    return row["id"] if row else hierarchy.create(db, sid, "Podcasts", by="lens-podcasts")


def regenerate(db, rid, by=None):
    """Make an episode again from what was asked for (the sources are read afresh). Returns the job."""
    get_row(db, rid)
    _save(db, rid, status="queued", error=None)
    return jobs.enqueue(db, rid, steps(), by=by)


# ---------- reading ----------
def get_row(db, rid):
    row = db.one("SELECT * FROM $r", r=R("podcast", int(rid)))
    if not row:
        raise KeyError(rid)
    row.pop("id", None)
    return row


def _save(db, rid, **fields):
    db.q("UPDATE $r MERGE $d", r=R("podcast", int(rid)), d={**fields, "updated_at": store.now()})


# ---------- the job's step ----------
def step(db, cfg, rid, say, spec=None):
    stage = (spec or {}).get("stage")
    if stage not in STAGES:
        raise ValueError(f"a podcast step's stage is one of {', '.join(STAGES)}")
    if not db.one("SELECT id FROM $r", r=R("podcast", rid)):
        raise jobs.Skip("this resource isn't a podcast episode")
    if stage in ("plan", "write", "check") and not llm.configured(cfg):
        raise Problem("no LLM is configured (Settings → Models)")
    _save(db, rid, status=STATUS[stage], error=None)
    try:
        {"gather": gather, "plan": plan, "write": write, "check": check, "audio": audio, "publish": publish}[stage](db, cfg, rid, say)
    except jobs.Skip:
        raise
    except Exception as e:
        _save(db, rid, status="failed", error=f"{STAGE_NAMES[stage]}: {e}"[:500])
        raise


# ---------- gather ----------
def _hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def runs(segs, size=RUN_CHARS):
    """Neighbouring lines (sorted by idx) grouped into runs of about `size` characters; a run never spans a gap in the
    lines or a page break."""
    out, cur, n = [], [], 0
    for s in segs:
        if cur and (s["idx"] != cur[-1]["idx"] + 1 or s.get("page") != cur[-1].get("page") or n + len(s["text"]) > size):
            out.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += len(s["text"]) + 1
    if cur:
        out.append(cur)
    return out


def _score(run, words):
    text = " ".join(s["text"] for s in run).lower()
    return sum(text.count(w) for w in words)


def pick(candidates, budget, words):
    """The runs worth the budget (characters): all of them when they fit; else those that mention the prompt's words
    most, then evenly spread over the rest so the whole source is covered. Kept in reading order."""
    size = lambda r: sum(len(s["text"]) + 1 for s in r)  # noqa: E731
    if sum(size(r) for r in candidates) <= budget:
        return candidates
    chosen, used = set(), 0
    if words:
        for i in sorted(range(len(candidates)), key=lambda i: -_score(candidates[i], words)):
            if _score(candidates[i], words) == 0 or used + size(candidates[i]) > budget:
                break
            chosen.add(i)
            used += size(candidates[i])
    rest = [i for i in range(len(candidates)) if i not in chosen]
    if rest and used < budget:
        # evenly spaced from the first to the last, then whatever else still fits
        k = max(1, min(len(rest), (budget - used) // max(1, sum(size(candidates[i]) for i in rest) // len(rest))))
        spaced = list(dict.fromkeys(rest[round(t * (len(rest) - 1) / (k - 1))] if k > 1 else rest[0] for t in range(k)))
        for i in spaced + [i for i in rest if i not in spaced]:
            if used + size(candidates[i]) <= budget:
                chosen.add(i)
                used += size(candidates[i])
    return [candidates[i] for i in sorted(chosen)]


def _excerpt(n, rid, run, rec, names, space_names, picked=True):
    first = run[0]
    page = first.get("page")
    text = "\n".join(
        s["text"] if s.get("page") is not None or not s.get("speaker") else f"{names.get(s['speaker'], 'Unknown')}: {s['text']}"
        for s in run
    )
    return store.clean(
        {
            "n": n,
            "ref": {"kind": "segment", "recording": rid, "idx0": first["idx"], "idx1": run[-1]["idx"]},
            "title": rec.get("title") or "Untitled",
            "namespace": space_names.get(rec.get("space")),
            "at": store.tc(first["t0"]) if page is None else f"p. {page + 1}",
            "t0": first["t0"],
            "page": page,
            "text": text,
            "hash": _hash(text),
            "picked": picked,
        }
    )


def resolve(db, cfg, sel, prompt=None, readable=None, budget=None, also=()):
    """The picked recordings and lines as numbered excerpts, within `budget` characters shared fairly between the
    sources. A picked line comes with its neighbours. Recordings outside `readable` (None: all) and not in `also` are
    left out."""
    budget = int(budget or (cfg.get("podcasts") or {}).get("context_chars") or 24000)
    rids = selected_recordings(sel)
    recs = {
        r["id"]: r
        for r in db.rows("SELECT record::id(id) AS id, title, space FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in rids])
        if readable is None or r["space"] in readable or r["id"] in set(also or ())
    }
    if not recs:
        raise Problem("none of the picked sources can be read any more")
    whole = [r for r in sel.get("recordings") or [] if r in recs]
    lines = {}
    for x in sel.get("excerpts") or []:
        if x["recording"] in recs:
            lines.setdefault(x["recording"], set()).update({x["idx"] - 2, x["idx"] - 1, x["idx"], x["idx"] + 1, x["idx"] + 2})
    words = chat.keywords(prompt or "")
    share = budget // max(1, len(whole) + len(lines))
    chosen = []  # (rid, run)
    for rid, idxs in lines.items():
        segs = db.rows(
            "SELECT idx, t0, t1, speaker, text, page FROM segment WHERE recording = $r AND idx IN $i ORDER BY idx",
            r=rid,
            i=sorted(i for i in idxs if i >= 0),
        )
        chosen += [(rid, r) for r in pick(runs(segs, size=10**9), share, words)]
    for rid in whole:
        segs = db.rows("SELECT idx, t0, t1, speaker, text, page FROM segment WHERE recording = $r ORDER BY idx", r=rid)
        chosen += [(rid, r) for r in pick(runs(segs), share, words)]
    if not chosen:
        raise Problem("the picked sources have no text yet (are they still being processed?)")
    names = render.speaker_names(db, [s.get("speaker") for _, r in chosen for s in r])
    space_names = store.space_names(db)
    order = {rid: k for k, rid in enumerate(rids)}
    chosen.sort(key=lambda x: (order[x[0]], x[1][0]["idx"]))
    return [_excerpt(n, rid, run, recs[rid], names, space_names) for n, (rid, run) in enumerate(chosen, 1)]


def gather(db, cfg, rid, say):
    p = get_row(db, rid)
    req = p["request"]
    readable = set(p["readable"]) if p.get("readable") is not None else None
    sources = resolve(db, cfg, req["selection"], req.get("prompt"), readable, also=p.get("also") or ())
    _save(db, rid, sources=sources, outline=None, lines=None, checks=None, audio=None)
    say(f"gathered {len(sources)} excerpt(s) from {len({s['ref']['recording'] for s in sources})} source(s)")


# ---------- prompts ----------
OUTLINE_SCHEMA = {
    "type": "object",
    "required": ["title", "ideas", "questions", "connections", "recap"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["point", "sources"],
                "properties": {"point": {"type": "string"}, "sources": {"type": "array", "items": {"type": "integer"}}},
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
        "connections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim", "sources"],
                "properties": {"claim": {"type": "string"}, "sources": {"type": "array", "items": {"type": "integer"}}},
            },
        },
        "recap": {"type": "array", "items": {"type": "string"}},
    },
}
SCRIPT_SCHEMA = {
    "type": "object",
    "required": ["lines"],
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["speaker", "kind", "text", "citations"],
                "properties": {
                    "speaker": {"type": "string", "enum": ["a", "b"]},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "integer"}},
                },
            },
        }
    },
}
CHECK_SCHEMA = {
    "type": "object",
    "required": ["checks"],
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["line", "verdict"],
                "properties": {
                    "line": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["supported", "partial", "unsupported"]},
                    "why": {"type": "string"},
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "integer"}},
                },
            },
        }
    },
}
SYSTEM = (
    "You write and check scripts for a two-host learning podcast made from a private library. Use only the numbered "
    "excerpts you are given; never add facts from elsewhere. Reply with JSON only."
)
EXCERPTS = "Excerpts:\n{% for x in excerpts %}[{{ x.n }}] {{ x.title }} ({{ x.at }})\n{{ x.text }}\n\n{% endfor %}"
PLAN = (
    "Plan an episode of about {{ request.minutes }} minutes (about {{ request.words }} spoken words) for two hosts: "
    "{{ hosts.a }} explains, {{ hosts.b }} asks the questions a smart learner would ask.\n"
    "Style: {{ request.style_guide }}\n"
    "{% if request.prompt %}The listener asked for this angle: {{ request.prompt }}\n{% endif %}"
    "\nList the key ideas (each with the excerpt numbers it comes from), the learner questions to raise, 2 to 4 "
    "connections between excerpts (what links them, with their numbers), and the points of a closing recap. Give the "
    "episode a short title.\n\n" + EXCERPTS
)
WRITE = (
    "Write the script of the episode planned below, about {{ request.words }} words in all.\n"
    "Speaker a is {{ hosts.a }}, who explains; speaker b is {{ hosts.b }}, who asks what a smart learner would ask, "
    "pushes back and sums up. Use analogies, keep lines short and natural to say aloud, and end with a recap.\n"
    "Style: {{ request.style_guide }}\n"
    "{% if request.prompt %}The listener asked for this angle: {{ request.prompt }}\n{% endif %}"
    "\nEvery line has a kind: claim (states a fact), question, analogy, banter or recap. Every claim and recap line "
    "lists the numbers of the excerpts that support it in citations; don't state anything the excerpts don't say. "
    "Don't read citations or numbers aloud.\n\n"
    "Plan:\n{{ outline | json }}\n\n" + EXCERPTS
)
CHECK = (
    "Check each numbered script line against the excerpts it cites. Say supported when the excerpts say it, partial "
    "when they say part of it, unsupported when they don't. For partial and unsupported lines, give a corrected text "
    "that the excerpts do support (with its citations), or leave text out if nothing can be said.\n\n"
    "Lines:\n{% for l in lines %}{{ l.line }}. {{ l.text }} (cites {{ l.citations | join(', ') or 'nothing' }})\n{% endfor %}\n" + EXCERPTS
)
TEMPLATES = {
    "plan": ("Podcast: plan", "The outline of a podcast episode: ideas, questions, connections and a recap.", PLAN, OUTLINE_SCHEMA),
    "write": ("Podcast: write", "A podcast episode's two-host script, every claim cited.", WRITE, SCRIPT_SCHEMA),
    "check": ("Podcast: fact-check", "Each claim in a podcast script checked against what it cites.", CHECK, CHECK_SCHEMA),
}


def template(db, stage):
    """The prompt template for a stage: the one whose role is podcast.<stage>, made from the built-in text the first
    time."""
    role = f"podcast.{stage}"
    row = db.one("SELECT record::id(id) AS id FROM template WHERE role = $r ORDER BY id LIMIT 1", r=role)
    if row:
        return templates.get(db, row["id"])
    name, desc, body, schema = TEMPLATES[stage]
    tid = templates.create(db, name, "prompt", body, schema, system=SYSTEM, description=desc, user="lens-podcasts")
    db.q("UPDATE $t SET role = $r", t=R("template", tid), r=role)
    return templates.get(db, tid)


def _ask(db, cfg, stage, ctx):
    t = template(db, stage)
    prompt = templates.render_body(t["body"], ctx, "prompt")
    model = (cfg.get("podcasts") or {}).get("model") or None
    value = llm.json_out(cfg, t.get("system") or SYSTEM, prompt, t.get("schema") or {"type": "object"}, model)
    return value, {"template": t["id"], "version": t["version"]}


def _context(cfg, p, **more):
    req = p["request"]
    wpm = int((cfg.get("podcasts") or {}).get("words_per_minute") or 150)
    return {
        "request": {
            "prompt": req.get("prompt"),
            "style": req["style"],
            "style_guide": STYLES[req["style"]],
            "minutes": req["length"],
            "words": req["length"] * wpm,
        },
        "hosts": req.get("hosts") or hosts(cfg),
        "excerpts": [{k: x.get(k) for k in ("n", "title", "at", "text")} for x in p.get("sources") or []],
        **more,
    }


# ---------- plan ----------
def _known(nums, known):
    out = []
    for n in nums or []:
        if isinstance(n, int) and not isinstance(n, bool) and n in known and n not in out:
            out.append(n)
    return out


def plan(db, cfg, rid, say):
    p = get_row(db, rid)
    if not p.get("sources"):
        raise Problem("nothing was gathered to plan from")
    out, used = _ask(db, cfg, "plan", _context(cfg, p))
    known = {s["n"] for s in p["sources"]}
    outline = {
        "title": (out.get("title") or "").strip()[:200] or None,
        "summary": (out.get("summary") or "").strip() or None,
        "ideas": [{"point": i["point"], "sources": _known(i.get("sources"), known)} for i in out.get("ideas") or [] if i.get("point")],
        "questions": [q for q in out.get("questions") or [] if isinstance(q, str) and q.strip()],
        "connections": [
            {"claim": c["claim"], "sources": _known(c.get("sources"), known)}
            for c in out.get("connections") or []
            if c.get("claim") and _known(c.get("sources"), known)
        ],
        "recap": [r for r in out.get("recap") or [] if isinstance(r, str) and r.strip()],
    }
    _save(db, rid, outline=store.clean(outline), templates={**(p.get("templates") or {}), "plan": used})
    say(f"planned {len(outline['ideas'])} idea(s), {len(outline['questions'])} question(s), {len(outline['connections'])} connection(s)")


# ---------- write ----------
def tidy(raw, known):
    """Script lines as the model wrote them, made safe to keep: known speakers and kinds, text trimmed, citations only to
    excerpts it was given. Returns (lines, problems): each problem says what was dropped."""
    lines, problems = [], []
    for k, l in enumerate(raw or []):
        text = " ".join(str(l.get("text") or "").split())
        if not text:
            problems.append(f"line {k}: empty")
            continue
        speaker = l.get("speaker") if l.get("speaker") in ("a", "b") else None
        if speaker is None:
            problems.append(f"line {k}: no speaker")
            continue
        cites = l.get("citations") or []
        kept = _known(cites, known)
        if len(kept) < len([c for c in cites if c is not None]):
            problems.append(f"line {k}: dropped citations to excerpts it wasn't given")
        kind = l.get("kind") if l.get("kind") in KINDS else ("claim" if kept else "banter")
        lines.append({"speaker": speaker, "kind": kind, "text": text[:LINE_CHARS], "citations": kept})
    for i, l in enumerate(lines):
        l["idx"] = i
    return lines, problems


def write(db, cfg, rid, say):
    p = get_row(db, rid)
    if not p.get("outline"):
        raise Problem("there's no outline to write from")
    out, used = _ask(db, cfg, "write", _context(cfg, p, outline=p["outline"]))
    lines, problems = tidy(out.get("lines"), {s["n"] for s in p["sources"]})
    if len(lines) < 2:
        raise Problem("the model wrote no usable script")
    _save(db, rid, lines=lines, checks=None, templates={**(p.get("templates") or {}), "write": used})
    for x in problems[:20]:
        say(x)
    say(f"wrote {len(lines)} line(s), {sum(1 for l in lines if l['citations'])} with citations")


# ---------- check ----------
def needs_check(line):
    return line["kind"] in CITED or bool(line["citations"])


def _verdicts(db, cfg, p, lines):
    """{line idx: check} from the model for these lines, with only the excerpts they cite."""
    cited = {n for l in lines for n in l["citations"]}
    shown = {**p, "sources": [s for s in p["sources"] if s["n"] in cited]}
    out, used = _ask(
        db, cfg, "check", _context(cfg, shown, lines=[{"line": l["idx"], "text": l["text"], "citations": l["citations"]} for l in lines])
    )
    want = {l["idx"] for l in lines}
    return {c["line"]: c for c in out.get("checks") or [] if c.get("line") in want}, used


def apply_checks(lines, verdicts, known, second=None):
    """The script after the fact-check, and what it changed. A supported line stays. A partial or unsupported line
    takes the corrected text when the checker gave one with citations it may use (a rewrite of an unsupported line has
    to pass `second`, a function {idx: line} -> {idx: verdict}, or it's dropped); otherwise a partial line stays and an
    unsupported one goes. A claim the checker skipped stays only when it has a citation."""
    kept, log, rewritten = [], [], {}
    for l in lines:
        if not needs_check(l):
            kept.append(l)
            continue
        v = verdicts.get(l["idx"])
        verdict = (v or {}).get("verdict")
        why = (v or {}).get("why") or None
        if verdict == "supported" or (v is None and l["citations"]):
            kept.append(l)
            continue
        fix_text = " ".join(str((v or {}).get("text") or "").split())[:LINE_CHARS]
        fix_cites = _known((v or {}).get("citations"), known)
        if fix_text and fix_cites and verdict in ("partial", "unsupported"):
            new = {**l, "text": fix_text, "citations": fix_cites}
            if verdict == "unsupported":
                rewritten[l["idx"]] = (l, new, why)
            else:
                log.append(
                    store.clean(
                        {"idx": l["idx"], "verdict": verdict, "action": "rewritten", "before": l["text"], "after": fix_text, "why": why}
                    )
                )
            kept.append(new)
        elif verdict == "partial":
            log.append(store.clean({"idx": l["idx"], "verdict": verdict, "action": "kept", "before": l["text"], "why": why}))
            kept.append(l)
        else:
            log.append(
                store.clean(
                    {
                        "idx": l["idx"],
                        "verdict": verdict or "unsupported",
                        "action": "dropped",
                        "before": l["text"],
                        "why": why or ("no citation" if not l["citations"] else None),
                    }
                )
            )
    if rewritten:
        again = second({i: new for i, (_, new, _) in rewritten.items()}) if second else {}
        for i, (old, new, why) in rewritten.items():
            ok = (again.get(i) or {}).get("verdict") in ("supported", "partial")
            log.append(
                store.clean(
                    {
                        "idx": i,
                        "verdict": "unsupported",
                        "action": "rewritten" if ok else "dropped",
                        "before": old["text"],
                        "after": new["text"] if ok else None,
                        "why": why,
                    }
                )
            )
            if not ok:
                kept = [l for l in kept if l["idx"] != i]
    log.sort(key=lambda c: c["idx"])
    return kept, log


def check(db, cfg, rid, say):
    p = get_row(db, rid)
    lines = p.get("lines") or []
    if not lines:
        raise Problem("there's no script to check")
    known = {s["n"] for s in p["sources"]}
    todo = [l for l in lines if needs_check(l) and l["citations"]]
    verdicts, used = _verdicts(db, cfg, p, todo) if todo else ({}, None)
    second = lambda new: _verdicts(db, cfg, p, list(new.values()))[0]  # noqa: E731
    kept, log = apply_checks(lines, verdicts, known, second)
    if len(kept) < 2:
        raise Problem("nothing in the script held up against its sources")
    for i, l in enumerate(kept):
        l["idx"] = i
    tpls = {**(p.get("templates") or {}), **({"check": used} if used else {})}
    _save(db, rid, lines=kept, checks=log, templates=tpls)
    changed = {a: sum(1 for c in log if c["action"] == a) for a in ("rewritten", "dropped")}
    say(f"checked {len(todo)} claim(s): {changed['rewritten']} rewritten, {changed['dropped']} dropped")


# ---------- publish ----------
def timed(lines, wpm=150, pause_ms=400):
    """[(t0, t1)] for lines read one after another at `wpm` words a minute with a pause between them."""
    out, t = [], 0
    for l in lines:
        d = max(800, int(len(l["text"].split()) / wpm * 60000))
        out.append((t, t + d))
        t += d + pause_ms
    return out


def voices(cfg, req=None):
    """{a, b}: the hosts' voices for the text-to-speech provider: what the episode asked for, else podcasts.voice_a /
    voice_b, else voice.tts_voice and a second voice of the provider's."""
    from . import speech

    v, p = cfg.get("voice") or {}, cfg.get("podcasts") or {}
    provider = v.get("tts_provider") or "openai"
    first = {"openai": v.get("tts_voice") or "alloy", "elevenlabs": v.get("tts_voice") or speech.ELEVENLABS_VOICE}.get(
        provider, v.get("tts_model") or speech.DEEPGRAM_TTS_MODEL
    )
    asked = (req or {}).get("voices") or {}
    return {"a": asked.get("a") or p.get("voice_a") or first, "b": asked.get("b") or p.get("voice_b") or SECOND_VOICE.get(provider, first)}


def folder(cfg, rid):
    """Where an episode's audio and the clips it's made of are kept."""
    return pathlib.Path(cfg["data_dir"]) / "podcasts" / str(int(rid))


def audio(db, cfg, rid, say):
    """Each line read aloud in its host's voice (clips are kept, so lines that didn't change aren't read again), joined
    with short pauses, its loudness evened out, and kept as the episode's audio. Without text to speech it stays a
    script."""
    from . import audio_mix, keyring, speech

    if not speech.tts_ready(cfg):
        raise jobs.Skip("no text to speech is set up (Settings → Voice), so the episode stays a script")
    p = get_row(db, rid)
    lines = p.get("lines") or []
    if not lines:
        raise Problem("there's no script to read")
    v = cfg.get("voice") or {}
    provider = v.get("tts_provider") or "openai"
    who = voices(cfg, p["request"])
    sid = p["space"]
    clips_dir = folder(cfg, rid) / "clips"
    clips, made = [], 0
    for k, l in enumerate(lines):
        voice = who[l["speaker"]]
        key = _hash(f"{provider}|{v.get('tts_model')}|{voice}|{l['text']}")
        clip = clips_dir / f"{key}.mp3"
        if clip.is_file():
            data = keyring.read_plain(db, cfg, clip)
        else:
            try:
                data, _ = speech.speak(cfg, l["text"], voice=voice)
            except speech.ProviderError as e:
                raise Problem(f"text to speech failed on line {k + 1}: {e}") from None
            keyring.keep(db, cfg, sid, clip, data)
            made += 1
        clips.append(audio_mix.decode(data))
        l["audio"] = key
        if (k + 1) % 10 == 0:
            say(f"read {k + 1} of {len(lines)} lines aloud")
    pauses = [0] + [PAUSE_MS["same" if lines[k]["speaker"] == lines[k - 1]["speaker"] else "turn"] for k in range(1, len(lines))]
    samples, spans = audio_mix.join(clips, pauses)
    data, ext, ctype = audio_mix.encode(samples)
    out = folder(cfg, rid) / f"episode.{ext}"
    for old in folder(cfg, rid).glob("episode.*"):
        if old != out:
            old.unlink(missing_ok=True)
    keyring.keep(db, cfg, sid, out, data)
    for l, (t0, t1) in zip(lines, spans):
        l["t0"], l["t1"] = t0, t1
    keep = {l["audio"] for l in lines}
    for f in clips_dir.glob("*.mp3"):  # clips of lines the script no longer has
        if f.stem not in keep:
            f.unlink(missing_ok=True)
    meta = {"path": str(out), "type": ctype, "duration_ms": spans[-1][1], "provider": provider, "voices": who}
    _save(db, rid, lines=lines, audio=meta)
    say(f"recorded {len(lines)} line(s) ({made} new) in {store.tc(spans[-1][1])}")


def publish(db, cfg, rid, say):
    from . import ingest, keyring, speakers as spk

    p = get_row(db, rid)
    lines = p.get("lines") or []
    if not lines:
        raise Problem("there's no script to publish")
    names = p["request"].get("hosts") or hosts(cfg)
    wpm = int((cfg.get("podcasts") or {}).get("words_per_minute") or 150)
    sound = p.get("audio") if (p.get("audio") or {}).get("path") and all("t0" in l for l in lines) else None
    spans = [(l["t0"], l["t1"]) for l in lines] if sound else timed(lines, wpm)
    segs = [{"t0": t0, "t1": t1, "text": l["text"], "speaker": names[l["speaker"]]} for l, (t0, t1) in zip(lines, spans)]
    rec = db.one("SELECT space, title FROM $r", r=R("recording", rid))
    title = ((p.get("outline") or {}).get("title") or "").strip()
    patch = {
        "title": title[:200] if title else rec.get("title"),
        "duration_ms": segs[-1]["t1"],
        "status": "transcribed",
        "transcribed_at": store.now(),
        "source": "audio" if sound else "transcript",
        "path": sound["path"] if sound else f"podcast:{rid}",
    }
    if sound:
        patch["channels"] = 1
        patch["envelope"] = ingest.envelope(ingest.decode(keyring.working_copy(db, cfg, sound["path"])))
    ingest.write_transcript(db, rid, rec["space"], segs, store.clean(patch))
    if not sound:  # an episode made again without voices: no waveform of the old audio
        db.q("UPDATE $r SET envelope = NONE, channels = NONE", r=R("recording", rid))
    spk.assign_labels(db, rec["space"], rid, {v: v for v in {s["speaker"] for s in segs}})
    _save(db, rid, status="ready" if sound else "script_only")
    say(f"published {len(segs)} line(s) as the episode's transcript" + (" with its audio" if sound else ""))


# ---------- views ----------
def view(db, rid):
    """An episode: what was asked, its status, the excerpts it drew on, the outline, the lines with their citations and
    the fact-check log."""
    p = get_row(db, rid)
    rec = db.one("SELECT title, space, duration_ms, status FROM $r", r=R("recording", int(rid))) or {}
    by_n = {s["n"]: s for s in p.get("sources") or []}
    lines = [
        {
            **l,
            "sources": [
                store.clean(by_n[n]["ref"] | {"n": n, "t0": by_n[n].get("t0"), "page": by_n[n].get("page")})
                for n in l["citations"]
                if n in by_n
            ],
        }
        for l in p.get("lines") or []
    ]
    return store.clean(
        {
            "id": int(rid),
            "title": rec.get("title"),
            "space": p.get("space"),
            "status": p.get("status"),
            "error": p.get("error"),
            "request": p.get("request"),
            "sources": p.get("sources") or [],
            "outline": p.get("outline"),
            "lines": lines,
            "checks": p.get("checks") or [],
            "templates": p.get("templates"),
            "audio": {k: v for k, v in (p.get("audio") or {}).items() if k != "path"} or None,
            "duration_ms": rec.get("duration_ms"),
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at"),
        }
    )


def list_episodes(db, spaces=None, limit=50, offset=0):
    q = "SELECT recording, space, status, created_at, updated_at FROM podcast"
    rows = db.rows(
        q + (" WHERE space IN $s" if spaces is not None else "") + " ORDER BY created_at DESC LIMIT $l START $o",
        s=sorted(spaces or []),
        l=int(limit),
        o=int(offset),
    )
    titles = (
        {
            r["id"]: r
            for r in db.rows(
                "SELECT record::id(id) AS id, title, duration_ms FROM recording WHERE id IN $ids",
                ids=[R("recording", r["recording"]) for r in rows],
            )
        }
        if rows
        else {}
    )
    return [
        store.clean(
            {
                "id": r["recording"],
                **{k: r.get(k) for k in ("space", "status", "created_at", "updated_at")},
                **{k: (titles.get(r["recording"]) or {}).get(k) for k in ("title", "duration_ms")},
            }
        )
        for r in rows
    ]
