"""For you: the timeline in an order learned from each person's own signals (docs/timeline.md, For you).

Latest is the stream as it is, newest first. For you takes a page's worth of the newest items (up to WINDOW) and
reorders it by a cheap local score, recency times affinity: how much the person has reacted to, commented on and
marked important items from the same actor, of the same kind and about the same entities, less what they hid. Each
item's `why` says which of those put it where it is, so the order is explainable. No model call: three small queries
for the person's signals, one look-up of the activities they were on, and arithmetic. Signals are the person's own
(their reactions, comments and marks), never anyone else's, and nothing is stored: the score is made on each read.
"""

from __future__ import annotations

import math

from . import events

HALF_LIFE_HOURS = 48.0  # an item's recency halves every two days
WINDOW = 200  # the newest items one page scores (the most events.page gives)
SIGNALS = 200  # the most recent rows of each kind of signal that count
WEIGHTS = {"reaction": 1.0, "comment": 2.0, "important": 2.0, "hidden": -3.0}
SCALE = 4.0  # affinity this strong doubles (or, negative, halves) an item's score
ORDERS = ("latest", "for_you")


def signals(db, account):
    """[(activity id, weight)]: the person's most recent reactions, comments (on an activity, or on an asset, which is
    on its arrival) and important or hidden marks, each weighed."""
    if account is None:
        return []
    out = []
    for r in db.rows(f"SELECT activity, at FROM event_reaction WHERE account = $a ORDER BY at DESC LIMIT {SIGNALS}", a=account):
        out.append((r["activity"], WEIGHTS["reaction"]))
    for r in db.rows(
        f"SELECT activity, recording, created_at FROM comment WHERE account = $a AND author IS NONE ORDER BY created_at DESC LIMIT {SIGNALS}",
        a=account,
    ):
        aid = r.get("activity") or (f"create-recording-{r['recording']}" if r.get("recording") is not None else None)
        if aid:
            out.append((aid, WEIGHTS["comment"]))
    for r in db.rows(
        "SELECT activity, important, hidden, at FROM event_mark WHERE account = $a AND (important = true OR hidden = true) "
        f"ORDER BY at DESC LIMIT {SIGNALS}",
        a=account,
    ):
        if r.get("hidden"):
            out.append((r["activity"], WEIGHTS["hidden"]))
        elif r.get("important"):
            out.append((r["activity"], WEIGHTS["important"]))
    return [(a, w) for a, w in out if isinstance(a, str)]


def affinity(db, cfg, who):
    """{actor: {id: weight}, kind: {name: weight}, entity: {id: (weight, name)}, n}: the person's signals summed
    over the actor, kind and tagged entities of the activities they were on; `n` is how many counted."""
    sig = signals(db, who.get("account"))
    acts = events.get_many(db, cfg, [a for a, _ in sig], who) if sig else {}
    out = {"actor": {}, "kind": {}, "entity": {}, "n": 0}
    for aid, w in sig:
        a = acts.get(aid)
        if not a:
            continue
        out["n"] += 1
        actor = (a.get("actor") or {}).get("id")
        if actor:
            out["actor"][actor] = out["actor"].get(actor, 0.0) + w
        out["kind"][a["kind"]] = out["kind"].get(a["kind"], 0.0) + w
        for t in a.get("tag") or []:
            if isinstance(t, dict) and t.get("id"):
                out["entity"][t["id"]] = out["entity"].get(t["id"], 0.0) + w
    return out


def _label(kind):
    try:
        return events.kind_info(kind)["label"].lower()
    except KeyError:
        return kind


def _why(what, name, w):
    if w > 0:
        return {
            "actor": f"You often react to {name}",
            "kind": f"You often react to {name} items",
            "entity": f"About {name}, which you often react to",
        }[what]
    return {
        "actor": f"You hid items from {name} before",
        "kind": f"You hid {name} items before",
        "entity": f"You hid items about {name} before",
    }[what]


def score(a, aff, now):
    """(score, why) for one activity: recency (half-life HALF_LIFE_HOURS) times 1 + tanh(affinity / SCALE), with
    affinity the sum of the person's weights on its actor, kind and entities; `why` names the strongest of them, or
    is None when none of them has a weight."""
    try:
        age = max(0.0, (now - events._parse(a["published"])).total_seconds()) / 3600
    except (KeyError, TypeError, ValueError):
        age = 0.0
    recency = 0.5 ** (age / HALF_LIFE_HOURS)
    parts = []
    actor = a.get("actor") or {}
    w = aff["actor"].get(actor.get("id"), 0.0)
    if w:
        parts.append((w, "actor", actor.get("name") or actor.get("id")))
    w = aff["kind"].get(a.get("kind"), 0.0)
    if w:
        parts.append((w, "kind", _label(a["kind"])))
    for t in a.get("tag") or []:
        w = aff["entity"].get(t.get("id"), 0.0) if isinstance(t, dict) else 0.0
        if w:
            parts.append((w, "entity", t.get("name") or t.get("id")))
    total = sum(p[0] for p in parts)
    why = None
    if parts:
        w, what, name = max(parts, key=lambda p: abs(p[0]))
        why = _why(what, name, w)
    return recency * (1 + math.tanh(total / SCALE)), why


def rank(items, aff, now=None):
    """The activities best first by `score`, ties the newest first: [(activity, why)]."""
    now = now or events._parse(events.stamp())
    scored = [(score(a, aff, now), a) for a in items]
    scored.sort(key=lambda x: (x[0][0], x[1].get("published") or ""), reverse=True)
    return [(a, why) for (_, why), a in scored]


def page(db, cfg, who, flt, before, limit, keep=None):
    """A For you page: the WINDOW newest items from `before` on (or `limit`, when more), reordered by score, the best
    `limit` of them, with `why` by id for those a signal placed; `next` moves past the whole window, so Older reads
    on from where the window ended."""
    limit = max(1, min(int(limit), WINDOW))
    got = events.page(db, cfg, who, before, flt, max(limit, WINDOW), keep)
    ranked = rank(got["items"], affinity(db, cfg, who))[:limit]
    return {"items": [a for a, _ in ranked], "next": got["next"], "why": {a["id"]: w for a, w in ranked if w}}
