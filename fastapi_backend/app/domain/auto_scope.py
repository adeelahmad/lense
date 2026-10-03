"""A conversation started over everything (the assistant home) gets the namespace its first question is about.

Nobody has to pick: the decision model (decide.py) chooses from the namespaces the person can read, given the question,
what each namespace holds and how many matching excerpts each one has. Only a sure choice narrows the conversation;
otherwise it stays over everything. A scope the person chose is never changed, and later questions are left alone,
so widening it again sticks.
"""

from __future__ import annotations

from collections import Counter

from . import decide, store
from .ops_tools import describe_namespace

QUESTION = (
    "Which namespace is this question about? Namespaces keep separate parts of someone's life or work apart; "
    "pick the one whose recordings would answer it."
)


def pick(db, cfg, question, readable, passages=()):
    """The decision ({"choice", "confidence", "ranked", "by"}) when it's sure, else None. `passages` are the excerpts
    that matched across every namespace."""
    mine = {n: sid for sid, n in store.space_names(db).items() if sid in readable}
    if len(mine) < 2:
        return None
    options = {n: describe_namespace(db, sid, n) for n, sid in sorted(mine.items())}
    hits = Counter(p.get("namespace") for p in passages)
    state = {
        "question": question[:4000],
        "matching_excerpts_per_namespace": {n: hits.get(n, 0) for n in options},
        "best_matches": [
            {"namespace": p.get("namespace"), "recording": p.get("title"), "text": str(p.get("text") or "")[:300]}
            for p in list(passages)[:6]
        ],
    }
    try:
        d = decide.choose(cfg, QUESTION, options, state)
    except decide.Undecided:
        return None
    return d if decide.sure(cfg, d) else None
