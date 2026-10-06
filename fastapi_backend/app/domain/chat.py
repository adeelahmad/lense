"""Questions answered from the archive, with numbered citations to the exact moments.

Retrieval is keyword-first over the full-text index (English stemming), joined by passages found by meaning when an
embedding model is set up (semantic.py), limited to the namespaces the asker can read and to the conversation's scope
(namespaces, recordings, collections, speakers, dates). Each hit is widened to its neighbouring lines and numbered; the model is told to answer only from those excerpts and cite them as [n]. With no model
configured, the best passages come back on their own.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict

from . import llm, recsets, render, semantic, store, textindex

R = store.R
STOP = set(
    """a about after again all also am an and any are as at be because been before being between both but by can could did do
does doing down during each few for from further had has have having he her here hers him his how i if in into is it its itself just
me more most my no nor not now of off on once only or other our ours out over own said same say says she should so some such tell
than that the their theirs them then there these they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours know think like really yeah okay right thing things get got going""".split()
)
SYSTEM = (
    "You answer questions about a private archive of recordings and transcripts. Use only the numbered excerpts you are "
    "given. Put the excerpt number in square brackets after each claim, like [2]. If the excerpts don't answer the question, "
    "say that the archive doesn't seem to cover it. Be concise."
)


def keywords(question, n=6):
    out = []
    for w in re.findall(r"[\w'’-]+", (question or "").lower()):
        w = w.strip("'’-")
        if len(w) >= 3 and w not in STOP and w not in out:
            out.append(w)
    return out[:n]


def scope_filter(db, spaces, scope):
    scope = scope or {}
    sp = set(spaces)
    if scope.get("namespaces"):
        sp &= {sid for sid, name in store.space_names(db).items() if name in scope["namespaces"]}
    where, p = ["space IN $sp"], {"sp": sorted(sp)}
    kept = recsets.within(db, sp, scope.get("recordings"), scope.get("collections"))
    recs = sorted(kept) if kept is not None else None
    if scope.get("from") or scope.get("to"):
        q = (
            "SELECT VALUE record::id(id) FROM recording WHERE space IN $sp"
            + (" AND recorded_at >= $f" if scope.get("from") else "")
            + (" AND recorded_at <= $t" if scope.get("to") else "")
        )
        dated = set(db.values(q, sp=sorted(sp), f=scope.get("from"), t=(scope.get("to") or "") + "T23:59:59"))
        recs = [r for r in recs if r in dated] if recs is not None else sorted(dated)
    if recs is not None:
        where.append("recording IN $recs")
        p["recs"] = recs
    if scope.get("speakers"):
        where.append("speaker IN $spk")
        p["spk"] = [int(s) for s in scope["speakers"]]
    return " AND ".join(where), p


def retrieve(db, question, spaces, scope=None, k=8, cfg=None):
    """The excerpts that best answer a question: lines with its keywords and, when search by meaning is set up
    (`cfg`), passages about it in other words, ranked together by reciprocal rank and widened to whole runs of lines."""
    words = keywords(question)
    meaning = cfg is not None and semantic.available(db, cfg)
    if not (words or meaning) or not spaces:
        return []
    where, p = scope_filter(db, spaces, scope)
    hits = {}
    for w in words:
        rows = textindex.rows(db, "segment", textindex.words(w), "record::id(id) AS id, recording, idx", f" AND {where}", p, 50)
        for r in rows or []:
            r["s"] = r.pop("s1")
        if rows is None and db.ready_fulltext():
            try:
                rows = db.rows(
                    f"SELECT record::id(id) AS id, recording, idx, search::score(1) AS s FROM segment WHERE text @1@ $w AND {where} LIMIT 50",
                    w=w,
                    **p,
                )
            except Exception:  # noqa: BLE001 - fall back to a plain scan
                rows = None
        if rows is None:
            rows = db.rows(
                f"SELECT record::id(id) AS id, recording, idx FROM segment WHERE string::contains(string::lowercase(text), $w) AND {where} LIMIT 50",
                w=w,
                **p,
            )
        for r in rows:
            h = hits.setdefault(r["id"], {"recording": r["recording"], "idx": r["idx"], "score": 0.0, "words": set()})
            h["score"] += abs(r.get("s") or 1.0)
            h["words"].add(w)
    top = sorted(hits.values(), key=lambda h: (-len(h["words"]), -h["score"]))[:k]
    # each excerpt ranks by its best line with the words plus its best passage found by meaning, by reciprocal rank
    wanted, best, meant = defaultdict(set), {}, {}
    for i, h in enumerate(top):
        wanted[h["recording"]].update({h["idx"] - 1, h["idx"], h["idx"] + 1})
        best[(h["recording"], h["idx"])] = 1 / (60 + i + 1)
    if meaning:
        try:
            near = semantic.nearest(
                db, cfg, question, " AND " + where.replace("speaker IN $spk", "speakers CONTAINSANY $spk"), p, {"said", "page"}, k
            )
        except semantic.EmbedError:  # the excerpts with the words still answer
            near = []
        for i, x in enumerate(near):
            span = range(x["idx0"], x["idx1"] + 1)
            wanted[x["recording"]].update(span)
            for j in span:
                meant.setdefault((x["recording"], j), 1 / (60 + i + 1))
    passages = []
    for rid, idxs in wanted.items():
        segs = db.rows(
            "SELECT idx, t0, t1, speaker, text, page FROM segment WHERE recording = $r AND idx IN $i ORDER BY idx",
            r=rid,
            i=sorted(i for i in idxs if i >= 0),
        )
        run = []
        for s in segs + [None]:
            if s and run and s["idx"] == run[-1]["idx"] + 1:
                run.append(s)
                continue
            if run:
                passages.append(
                    {
                        "recording_id": rid,
                        "segs": run,
                        "rank": max(best.get((rid, x["idx"]), 0) for x in run) + max(meant.get((rid, x["idx"]), 0) for x in run),
                    }
                )
            run = [s] if s else []
    passages.sort(key=lambda x: -x["rank"])
    names = render.speaker_names(db, [s.get("speaker") for x in passages for s in x["segs"]])
    recs = (
        {
            r["id"]: r
            for r in db.rows(
                "SELECT record::id(id) AS id, title, recorded_at, space FROM recording WHERE id IN $ids",
                ids=[R("recording", i) for i in wanted],
            )
        }
        if wanted
        else {}
    )
    spaces_n, out = store.space_names(db), []
    for n, x in enumerate(passages, 1):
        rec = recs.get(x["recording_id"], {})
        first = x["segs"][0]
        page = first.get("page")  # a document's text is on pages, without speakers
        out.append(
            store.clean(
                {
                    "n": n,
                    "recording_id": x["recording_id"],
                    "title": rec.get("title"),
                    "namespace": spaces_n.get(rec.get("space")),
                    "recorded_at": rec.get("recorded_at"),
                    "t0": first["t0"],
                    "time": store.tc(first["t0"]) if page is None else f"p. {page + 1}",
                    "page": page,
                    "speaker": names.get(first.get("speaker")),
                    "text": "\n".join(
                        s["text"] if s.get("page") is not None else f"{names.get(s.get('speaker'), 'Unknown')}: {s['text']}"
                        for s in x["segs"]
                    ),
                }
            )
        )
    if not (scope or {}).get("speakers"):  # text shown on screen in videos
        seen = Counter()
        where_o = where.replace("speaker IN $spk", "true")
        for w in words:
            try:
                rows = textindex.rows(
                    db, "ocr_span", textindex.words(w), "record::id(id) AS id, recording, t0, text, space", f" AND {where_o}", p, 20
                )
                if rows is None:
                    rows = db.rows(
                        f"SELECT record::id(id) AS id, recording, t0, text, space FROM ocr_span WHERE text @1@ $w AND {where_o} LIMIT 20",
                        w=w,
                        **p,
                    )
            except Exception:  # noqa: BLE001
                rows = [
                    r
                    for r in db.rows(
                        f"SELECT record::id(id) AS id, recording, t0, text, space FROM ocr_span WHERE {where_o} LIMIT 2000", **p
                    )
                    if w in r["text"].lower()
                ]
            for r in rows:
                seen[(r["id"], r["recording"], r["t0"], r["text"], r["space"])] += 1
        extra = (
            {
                r["id"]: r
                for r in db.rows(
                    "SELECT record::id(id) AS id, title, recorded_at FROM recording WHERE id IN $ids",
                    ids=[R("recording", k[1]) for k in seen],
                )
            }
            if seen
            else {}
        )
        for (oid, rid, t0, text, space), _ in seen.most_common(3):
            out.append(
                {
                    "n": len(out) + 1,
                    "recording_id": rid,
                    "title": extra.get(rid, {}).get("title"),
                    "namespace": spaces_n.get(space),
                    "recorded_at": extra.get(rid, {}).get("recorded_at"),
                    "t0": t0,
                    "time": store.tc(t0),
                    "speaker": None,
                    "source": "screen",
                    "text": f"On screen: {text}",
                }
            )
    return out


PAGE_TEXT, PAGE_SELECTION = 12000, 4000


def shared_context(ctx):
    """What a question keeps of the page it was asked from: where, its title, the highlighted text, and whether the
    page's text was shared (the text itself isn't kept)."""
    if not ctx or not ctx.get("url"):
        return None
    return store.clean(
        {
            "url": str(ctx["url"])[:2000],
            "title": (ctx.get("title") or "").strip()[:300] or None,
            "selection": (ctx.get("selection") or "").strip()[:PAGE_SELECTION] or None,
            "page": bool((ctx.get("text") or "").strip()) or None,
        }
    )


def with_context(question, ctx):
    """The question as the model reads it when it was asked from a page: the page, the highlighted text and (when
    shared) the page's text come first, marked as what the person is looking at in Lens rather than archive excerpts."""
    if not ctx or not ctx.get("url"):
        return question
    title = (ctx.get("title") or "").strip()[:300]
    parts = [f"The person is looking at the Lens page {title + ' ' if title else ''}({str(ctx['url'])[:2000]})."]
    sel = (ctx.get("selection") or "").strip()[:PAGE_SELECTION]
    if sel:
        parts.append(f'They highlighted this part of it:\n"""\n{sel}\n"""')
    text = (ctx.get("text") or "").strip()
    if text:
        cut = text[:PAGE_TEXT]
        more = " (cut short)" if len(text) > PAGE_TEXT else ""
        parts.append(f'The page\'s text{more}:\n"""\n{cut}\n"""')
    parts.append(f"Use the page to understand the question; it is not an archive excerpt, so don't cite it with [n]. Question: {question}")
    return "\n\n".join(parts)


def past_turns(history, n=6, summary=None):
    """The conversation's last messages for the model, with the files sent with each; a question asked about
    highlighted text keeps (the start of) that text, so a follow-up still knows what "it" was. With a `summary`
    (compaction is on), every message it doesn't cover yet, up to RECENT + COMPACT_AFTER of them."""
    if summary is not None:
        history = [m for m in history if m["id"] > (summary.get("upto") or 0)]
        n = RECENT + COMPACT_AFTER
    out = []
    for m in list(history)[-n:]:
        sel = ((m.get("context") or {}).get("selection") or "")[:500] if m["role"] == "user" else ""
        said = f'(About the highlighted text: "{sel}") {m["content"]}' if sel else m["content"]
        out.append({"role": m["role"], "content": said + attached_note(m.get("attachments"))})
    return out


def messages_for(question, passages, history=(), summary=None):
    ctx = "\n\n".join(f"[{p['n']}] {p['title']} · {(p.get('recorded_at') or '')[:10]} · {p['time']}\n{p['text']}" for p in passages)
    msgs = [{"role": "system", "content": SYSTEM + summary_note(summary)}]
    msgs += past_turns(history, summary=summary)
    msgs.append({"role": "user", "content": f"Excerpts:\n\n{ctx or '(nothing in the archive matched)'}\n\nQuestion: {question}"})
    return msgs


def fallback(passages):
    if not passages:
        return "Nothing you can access in the archive matches that."
    lines = [f"[{p['n']}] {p['title']}, {p['time']}: {p['text'].splitlines()[0][:160]}" for p in passages[:5]]
    return "No language model is configured, so here are the passages that match best:\n" + "\n".join(lines)


def cited(text, passages):
    used = {int(n) for n in re.findall(r"\[(\d+)\]", text or "")}
    return [{**p, "used": p["n"] in used} for p in passages]


# ---------- conversations ----------
def create(db, account, title=None, scope=None, model=None, kind="chat"):
    """A conversation. A `setup` one (admins) is the assistant setting the server up: it acts instead of asking."""
    cid = db.next_id("chat")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("chat", cid),
        d=store.clean(
            {
                "account": account,
                "title": (title or ("Set up Lens" if kind == "setup" else "New conversation"))[:120],
                "kind": kind if kind != "chat" else None,
                "scope": scope or {},
                "model": model,
                "created_at": store.now(),
                "updated_at": store.now(),
            }
        ),
    )
    return cid


def get(db, cid, account):
    c = db.one(
        "SELECT record::id(id) AS id, account, title, scope, model, kind ?? 'chat' AS kind, created_at, updated_at FROM $r",
        r=R("chat", cid),
    )
    if not c or c["account"] != account:
        raise KeyError(cid)
    return c


def history(db, cid):
    return db.rows(
        "SELECT record::id(id) AS id, role, content, passages, created_at, stopped ?? false AS stopped, steps ?? [] AS steps, "
        "attachments ?? [] AS attachments, notice, error, check, model, context FROM chat_message WHERE chat = $c ORDER BY id",
        c=cid,
    )


def add(
    db, cid, role, content, passages=None, stopped=False, steps=None, notice=None, error=None, model=None, attachments=None, context=None
):
    """Save a message; an answer keeps the tool steps it took, any notice (e.g. the model can't use tools) and error; a
    question asked from a page keeps what it shared of it (shared_context)."""
    mid = db.next_id("chat_message")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("chat_message", mid),
        d=store.clean(
            {
                "chat": cid,
                "role": role,
                "content": content,
                "attachments": attachments or None,
                "passages": passages,
                "created_at": store.now(),
                "stopped": stopped or None,
                "steps": steps or None,
                "notice": notice,
                "error": error,
                "model": model,
                "context": context,
            }
        ),
    )
    db.q("UPDATE $r SET updated_at = $t", r=R("chat", cid), t=store.now())
    return mid


def rewind(db, cid, mid):
    """Remove a question of yours and everything said after it, to ask it again as edited; returns the old question
    (its content and the page context it was asked with)."""
    m = db.one("SELECT chat, role, content, context FROM $r", r=R("chat_message", mid))
    if not m or m["chat"] != cid or m["role"] != "user":
        raise KeyError(mid)
    db.q("DELETE chat_message WHERE chat = $c AND record::id(id) >= $m", c=cid, m=mid)
    # a summary that covers what was removed no longer holds: the messages left are folded in again
    db.q("UPDATE $r SET summary = NONE WHERE summary.upto >= $m", r=R("chat", cid), m=mid)
    return m


# ---------- compaction: a long conversation keeps a summary of what the model no longer sees word for word ----------
RECENT = 6  # the latest messages, always given word for word
COMPACT_AFTER = 8  # how many more build up before they're folded into the summary (one model call per that many)
TURN_CHARS = 1500  # of each message, for the summary
COMPACT_SYSTEM = (
    "You keep the running summary of a conversation between a person and their assistant, so the assistant still "
    "knows what was said once the messages themselves are out of view. Fold the new messages into the summary so far: "
    "what the person wants and prefers, facts and decisions settled, what the assistant did or proposed (and whether "
    "it was approved), and what is still open. Keep names, dates, numbers and namespaces exact; leave out excerpt "
    "numbers like [2], small talk and anything the newer messages replaced. Write short plain sentences, at most 250 "
    "words. Reply with the summary alone."
)


def memory(db, cfg, cid):
    """What compaction knows of a conversation: {"text", "upto": the last message it covers, "at"}, empty before the
    first summary; None when compaction is off (the model then sees the last 6 messages only, as before)."""
    if not cfg["ai"].get("compact", True) or not llm.configured(cfg):
        return None
    return (db.one("SELECT summary FROM $r", r=R("chat", cid)) or {}).get("summary") or {"text": "", "upto": 0}


def summary_note(summary):
    text = ((summary or {}).get("text") or "").strip()
    return f"\n\nEarlier in this conversation (a summary; the messages themselves are no longer shown):\n{text}" if text else ""


def _said(m):
    who = "Person" if m["role"] == "user" else "Assistant"
    text = (m.get("content") or "").strip()
    text = text[:TURN_CHARS] + (" (cut short)" if len(text) > TURN_CHARS else "")
    did = "; ".join(s.get("summary") or s.get("tool") or "" for s in m.get("steps") or [] if s)
    return f"{who}: {text}" + (f"\n(What it did: {did[:500]})" if did else "")


def compact(db, cfg, cid):
    """Fold the messages older than the latest RECENT into the conversation's summary, once COMPACT_AFTER of them have
    built up (ai.compact, on by default; needs a model). Returns the new summary, or None when nothing changed."""
    had = memory(db, cfg, cid)
    if had is None:
        return None
    fresh = [m for m in history(db, cid) if m["id"] > (had.get("upto") or 0)]
    if len(fresh) < RECENT + COMPACT_AFTER:
        return None
    old = fresh[:-RECENT]
    ask = f"Summary so far:\n{had.get('text') or '(none yet)'}\n\nNew messages:\n\n" + "\n\n".join(_said(m) for m in old)
    text = llm.chat(cfg, [{"role": "system", "content": COMPACT_SYSTEM}, {"role": "user", "content": ask}], max_tokens=800).strip()
    if not text:
        return None
    out = {"text": text, "upto": old[-1]["id"], "at": store.now()}
    db.q("UPDATE $r SET summary = $s", r=R("chat", cid), s=out)
    return out


def model_choices(cfg):
    """The models people may pick: the configured one first, then llm.chat_models, or (when that's empty) whatever
    the model server lists. Just the configured one when the server's list can't be had."""
    if not llm.configured(cfg):
        return []
    listed = list(cfg["llm"].get("chat_models") or [])
    if not listed:
        try:
            listed = llm.list_models(cfg)
        except llm.LLMError:
            listed = []
    return list(dict.fromkeys([cfg["llm"]["model"], *listed]))


def check_model(cfg, model):
    """A model someone picked, if it's one they may (ValueError otherwise); None for the configured one."""
    if model is None or model == cfg["llm"].get("model"):
        return None
    if model not in model_choices(cfg):
        raise ValueError(f"{model} isn't one of the models you can pick here")
    return model


class Answering:
    """An answer being written in a conversation. Stopping it (POST /chats/{cid}/stop, from any server process) sets a
    flag on the conversation, which the answer looks at between pieces and tool steps, at most every half second."""

    EVERY = 0.5

    def __init__(self, db, cid):
        self.db, self.r, self.last, self.stopped = db, R("chat", cid), 0.0, False
        db.q("UPDATE $r SET answering = true, stop_requested = false", r=self.r)

    def stop_requested(self, force=False):
        if not self.stopped and (force or time.monotonic() - self.last >= self.EVERY):
            self.last = time.monotonic()
            self.stopped = bool((self.db.one("SELECT stop_requested FROM $r", r=self.r) or {}).get("stop_requested"))
        return self.stopped

    def end(self):
        self.db.q("UPDATE $r SET answering = false, stop_requested = false", r=self.r)


def request_stop(db, cid):
    """Ask the answer being written in a conversation to stop; False when none is."""
    return bool(db.rows("UPDATE $r SET stop_requested = true WHERE answering = true RETURN id", r=R("chat", cid)))


TOOL_SYSTEM = (
    SYSTEM + " You can call tools to look things up in the archive. Cite moments with the [n] numbers the tools return, and "
    "only those. Tools that run work or change data need the person's approval: say what you proposed and that it's waiting for them. "
    "Notes are your notebook and need no approval: look in them first (find_notes, read_note), and when you learn something "
    "worth keeping, write or update a note (write_note, update_note), linking what it's about."
)


def attached_note(files):
    """What the model reads about the files sent with a question."""
    if not files:
        return ""
    lines = [f"- {f['filename']} ({round(f['size'] / 1e6, 1)} MB, attachment id {f['id']})" for f in files]
    return "\n\nAttached files, not in the archive yet (import_files puts them in a namespace, choosing one when not named):\n" + "\n".join(
        lines
    )


SETUP_SYSTEM = (
    "You are setting up this Lens server with its admin, who asked you to do it for them. Lens archives recordings, "
    "documents and images, transcribes and indexes them, and answers questions about them. Start with server_status and "
    "work through what's missing, most important first: a model provider (find_model_servers, then change_settings llm "
    "with the server's address and a chat model), a namespace, then search by meaning (an embedding model). Prefer "
    "sensible defaults and make the changes yourself; they're made as soon as you call the tool, and the admin can change "
    "them in Settings. Ask only what you can't decide (one short question at a time), and never for something a tool can "
    "find out. When files are attached, import them (leave the namespace out unless the admin named one: it's chosen "
    "for them, and you're told when to ask). Say in a sentence what you changed. Be brief."
)


def tool_answer(cfg, toolbox, question, history=(), max_steps=6, model=None, setup=False, summary=None):
    """The tool loop: yields ("step", {...}) for each tool call, then ("answer", text), or ("direct", text) when the model
    answered without looking anything up (it never saw the archive, so the caller can answer from a search instead).
    Raises llm.ToolsUnsupported."""
    system = TOOL_SYSTEM + ("\n\n" + SETUP_SYSTEM if setup else "")
    note = getattr(toolbox, "system_note", None)  # skills, and context from hooks (extensions.py)
    system += note() if note else ""
    system += summary_note(summary)
    msgs = [{"role": "system", "content": system}] + past_turns(history, summary=summary)
    msgs.append({"role": "user", "content": question})
    for step in range(max_steps):
        msg = llm.chat_message(cfg, msgs, tools=toolbox.specs(), model=model)
        if not msg["tool_calls"]:
            yield ("direct" if step == 0 else "answer"), msg["content"]
            return
        msgs.append({"role": "assistant", "content": msg["content"], "tool_calls": msg["tool_calls"]})
        for c in msg["tool_calls"]:
            fn = c.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # a JSON string, as OpenAI sends it; some servers send the object itself
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            result, summary = toolbox.call(fn.get("name", ""), args)
            yield "step", {"tool": fn.get("name"), "args": args, "summary": summary}
            msgs.append({"role": "tool", "tool_call_id": c.get("id"), "content": result})
    yield "answer", "I ran out of steps before finishing. Try a narrower question."


VERIFY = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim", "supported"],
                "properties": {"claim": {"type": "string"}, "supported": {"type": "boolean"}, "note": {"type": "string"}},
            },
        }
    },
}


def check_sources(cfg, answer, passages):
    """Re-check each cited claim against the lines it cites. Sentences without a citation are flagged as uncited."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", answer or "") if x.strip()]
    cited = [x for x in sentences if re.search(r"\[\d+\]", x)]
    uncited = [x for x in sentences if x not in cited and len(x.split()) > 3]
    if not cited:
        return {"claims": 0, "supported": 0, "verdicts": [], "uncited": uncited}
    ex = "\n\n".join(f"[{p['n']}] {p.get('title') or ''} · {p.get('time') or ''}\n{p['text']}" for p in passages)
    out = llm.json_out(
        cfg,
        "You check answers against sources. For each claim, decide whether the excerpts it cites support it. Be strict.",
        f"Excerpts:\n\n{ex}\n\nClaims:\n" + "\n".join(f"- {c}" for c in cited),
        VERIFY,
    )
    verdicts = out["verdicts"]
    return {"claims": len(cited), "supported": sum(1 for v in verdicts if v.get("supported")), "verdicts": verdicts, "uncited": uncited}
