"""A conversation started over everything (the assistant home) gets the namespace its first question is about.

Nobody has to pick: the decision model (decide.py) chooses from the namespaces the person can read, given the question,
what each namespace holds and how many matching excerpts each one has. Only a sure choice narrows the conversation.
When none is sure, the person gets the likeliest namespaces to tap; when none fits, admins also get new ones named for
the question. Nothing changes until they pick one. A scope the person chose is never changed, and
later questions are left alone, so widening it again sticks.
"""

from __future__ import annotations

from collections import Counter

from . import decide, llm, store
from .ops_tools import describe_namespace

QUESTION = (
    "Which namespace is this question about? Namespaces keep separate parts of someone's life or work apart; "
    "pick the one whose recordings would answer it, or none when it's about something none of them holds."
)
NONE = "none of these"
MAX_EXISTING = 2
MAX_NEW = 2


def _new_names(cfg, question, taken):
    """A couple of names for a namespace this question would belong in, from the language model ([] without one)."""
    if not llm.configured(cfg):
        return []
    schema = {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}, "required": ["names"]}
    try:
        out = llm.json_out(
            cfg,
            "You name namespaces: separate parts of someone's archive (family, a job, a podcast). Suggest two short "
            "names, one or two words, lowercase, words joined by -.",
            f"Question: {question[:2000]}\n\nNamespaces that already exist (don't repeat them): {', '.join(sorted(taken)) or 'none'}",
            schema,
        )
    except llm.LLMError:
        return []
    names = []
    for n in out.get("names") or []:
        n = "-".join(str(n).strip().lower().replace("_", " ").split())
        if store.NS_RX.match(n) and n not in taken and n not in names:
            names.append(n)
    return names[:MAX_NEW]


def place(db, cfg, question, readable, passages=(), admin=False):
    """("scoped", decision) when sure of a namespace; ("suggest", [{"name", "new"}]) when not, for the person to pick;
    None when there's nothing to say. `passages` are the excerpts that matched across every namespace."""
    names = store.space_names(db)
    mine = {n: sid for sid, n in names.items() if sid in readable}
    if len(mine) < 2 and not admin:
        return None
    options = {n: describe_namespace(db, sid, n) for n, sid in sorted(mine.items())}
    options[NONE] = "None of these namespaces holds what the question is about."
    hits = Counter(p.get("namespace") for p in passages)
    state = {
        "question": question[:4000],
        "matching_excerpts_per_namespace": {n: hits.get(n, 0) for n in mine},
        "best_matches": [
            {"namespace": p.get("namespace"), "recording": p.get("title"), "text": str(p.get("text") or "")[:300]}
            for p in list(passages)[:6]
        ],
    }
    try:
        d = decide.choose(cfg, QUESTION, options, state)
    except decide.Undecided:
        return None
    sure = decide.sure(cfg, d)
    if d["choice"] != NONE and sure:
        return ("scoped", d) if len(mine) > 1 else None
    # unsure: the likeliest namespaces; none fits (or likely none): new ones too, which only admins can create
    likely = [] if d["choice"] == NONE and sure else [r["option"] for r in d["ranked"] if r["option"] != NONE][:MAX_EXISTING]
    fresh = _new_names(cfg, question, set(names.values())) if admin and d["choice"] == NONE else []
    out = [{"name": n, "new": False} for n in likely] + [{"name": n, "new": True} for n in fresh]
    return ("suggest", out) if out else None
