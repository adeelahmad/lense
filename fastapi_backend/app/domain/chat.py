"""Questions answered from the archive, with numbered citations to the exact moments.

Retrieval is keyword-first over the full-text index (English stemming), limited to the namespaces the asker can read
and to the conversation's scope (namespaces, recordings, speakers, dates). Each hit is widened to its neighbouring
lines and numbered; the model is told to answer only from those excerpts and cite them as [n]. With no model
configured, the best passages come back on their own.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict

from . import llm, render, store

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
    recs = [int(r) for r in scope["recordings"]] if scope.get("recordings") else None
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


def retrieve(db, question, spaces, scope=None, k=8):
    words = keywords(question)
    if not words or not spaces:
        return []
    where, p = scope_filter(db, spaces, scope)
    hits = {}
    for w in words:
        rows = None
        if db.ready_fulltext():
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
    wanted, best = defaultdict(set), {}
    for h in top:
        wanted[h["recording"]].update({h["idx"] - 1, h["idx"], h["idx"] + 1})
        best[(h["recording"], h["idx"])] = (len(h["words"]), h["score"])
    passages = []
    for rid, idxs in wanted.items():
        segs = db.rows(
            "SELECT idx, t0, t1, speaker, text FROM segment WHERE recording = $r AND idx IN $i ORDER BY idx",
            r=rid,
            i=sorted(i for i in idxs if i >= 0),
        )
        run = []
        for s in segs + [None]:
            if s and run and s["idx"] == run[-1]["idx"] + 1:
                run.append(s)
                continue
            if run:
                passages.append({"recording_id": rid, "segs": run, "rank": max((best.get((rid, x["idx"]), (0, 0)) for x in run))})
            run = [s] if s else []
    passages.sort(key=lambda x: (-x["rank"][0], -x["rank"][1]))
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
        out.append(
            {
                "n": n,
                "recording_id": x["recording_id"],
                "title": rec.get("title"),
                "namespace": spaces_n.get(rec.get("space")),
                "recorded_at": rec.get("recorded_at"),
                "t0": first["t0"],
                "time": store.tc(first["t0"]),
                "speaker": names.get(first.get("speaker")),
                "text": "\n".join(f"{names.get(s.get('speaker'), 'Unknown')}: {s['text']}" for s in x["segs"]),
            }
        )
    if not (scope or {}).get("speakers"):  # text shown on screen in videos
        seen = Counter()
        where_o = where.replace("speaker IN $spk", "true")
        for w in words:
            try:
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


def messages_for(question, passages, history=()):
    ctx = "\n\n".join(f"[{p['n']}] {p['title']} · {(p.get('recorded_at') or '')[:10]} · {p['time']}\n{p['text']}" for p in passages)
    msgs = [{"role": "system", "content": SYSTEM}]
    msgs += [{"role": m["role"], "content": m["content"]} for m in list(history)[-6:]]
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
def create(db, account, title=None, scope=None):
    cid = db.next_id("chat")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("chat", cid),
        d=store.clean(
            {
                "account": account,
                "title": (title or "New conversation")[:120],
                "scope": scope or {},
                "created_at": store.now(),
                "updated_at": store.now(),
            }
        ),
    )
    return cid


def get(db, cid, account):
    c = db.one("SELECT record::id(id) AS id, account, title, scope, created_at, updated_at FROM $r", r=R("chat", cid))
    if not c or c["account"] != account:
        raise KeyError(cid)
    return c


def history(db, cid):
    return db.rows(
        "SELECT record::id(id) AS id, role, content, passages, created_at, stopped ?? false AS stopped, steps ?? [] AS steps, "
        "notice, error, check FROM chat_message WHERE chat = $c ORDER BY id",
        c=cid,
    )


def add(db, cid, role, content, passages=None, stopped=False, steps=None, notice=None, error=None):
    """Save a message; an answer keeps the tool steps it took, any notice (e.g. the model can't use tools) and error."""
    mid = db.next_id("chat_message")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("chat_message", mid),
        d=store.clean(
            {
                "chat": cid,
                "role": role,
                "content": content,
                "passages": passages,
                "created_at": store.now(),
                "stopped": stopped or None,
                "steps": steps or None,
                "notice": notice,
                "error": error,
            }
        ),
    )
    db.q("UPDATE $r SET updated_at = $t", r=R("chat", cid), t=store.now())
    return mid


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
    "only those. Tools that run work or change data need the person's approval: say what you proposed and that it's waiting for them."
)


def tool_answer(cfg, toolbox, question, history=(), max_steps=6):
    """The tool loop: yields ("step", {...}) for each tool call, then ("answer", text). Raises llm.ToolsUnsupported."""
    msgs = [{"role": "system", "content": TOOL_SYSTEM}] + [{"role": m["role"], "content": m["content"]} for m in list(history)[-6:]]
    msgs.append({"role": "user", "content": question})
    for _ in range(max_steps):
        msg = llm.chat_message(cfg, msgs, tools=toolbox.specs())
        if not msg["tool_calls"]:
            yield "answer", msg["content"]
            return
        msgs.append({"role": "assistant", "content": msg["content"], "tool_calls": msg["tool_calls"]})
        for c in msg["tool_calls"]:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
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
