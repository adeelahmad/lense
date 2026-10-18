"""Organising the entity graph with a workflow: graph workflows (scope `graph`) run over namespaces rather than over one
recording, usually from a routine, and propose or make changes to the entities in them.

Their nodes, besides the primitives every workflow has (flow.py: input, pick, condition, switch, merge, set, template,
filter, for each, repeat, group and custom nodes):

- `candidates`: what may need tidying, found by rules, by `kind`:
  - merge: pairs of entities in a namespace that may be one thing (same letters, acronym, spelling, sounds alike, one
    name inside the other); link: such pairs across namespaces whose graph is shared (the global graph already joins
    names that are exactly the same). Pairs someone said are different are left out.
  - retype: entities whose type the rules (name_types.py) now read differently, from their name and every line that
    says them (a name analysis called a term in its first recording, and later lines show is a person). Types a person
    or an agent set are left as they are.
  - hide: entities that are noise: no letters, one letter, filler words ("Okay so"), a phrase rather than a name, or a
    term said only once. Entities someone showed again are left out.
  - cluster: a namespace's topics to group under a broader one: those whose labels end in another topic's label
    ("Machine learning", "Deep learning" under "Learning"), or several with no broader topic that end in the same word
    ("Sales calls", "Support calls", "Team calls" under a new "Calls").
  Candidates with a change already proposed, dismissed or undone are left out, so a run doesn't redo what someone
  took back.
- `llm_judge`: asks the model, a batch at a time with lines where each name was said, about each candidate: whether a
  pair is the same thing, what type a name is, whether it is noise, or whether topics go under a broader one. Each
  gets a `verdict` {agree (make the change), confidence, why; for pairs same and keep; for a retype the type; for a
  group a better label for a new broader topic and the topics to leave out}.
- `apply_changes`: makes the changes it is given (merges, links, new types, hides, groups), or proposes them for someone to
  accept. Only those whose confidence (the model's, else the rules') is at least `apply_above` are made, at most
  `max_apply` a run; without `apply_above` everything is proposed. Every change is recorded and can be undone, one by
  one or a whole run.

Each change is a `graph_change` row: {kind, pair (what it is about, to tell it again), a (the entity, a pair's first,
or a group's broader topic {id (none for a new one), name}), b, keep, to (a retype's new type), topics (a group's
[{id, name, recordings}]), spaces, reason, confidence, verdict, status (proposed, applied, dismissed, undone), merge (the
entity_merge id behind an applied merge), parent, created, grouped (an applied group's broader topic, whether the
group made it, and the topics put under it), routine, run, workflow}. Topics aren't in the graph's history, so a group
is undone only from its change.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import re

from . import analyze, entities, entity_setup, graph_history, llm, name_types, notebook, store, topics

R = store.R
GRAPH_NODES = ("input", "pick", "condition", "merge", "candidates", "llm_judge", "filter", "apply_changes")
OWN_NODES = ("candidates", "llm_judge", "apply_changes")
CONFIG = {
    "candidates": {"kind", "min_confidence", "limit", "types"},
    "llm_judge": {"model", "instructions", "batch"},
    "filter": {"path", "op", "value", "match"},
    "apply_changes": {"apply_above", "max_apply"},
}
PAIRS = ("merge", "link")  # kinds about two entities; the rest are about one
KINDS = (*PAIRS, "retype", "hide", "cluster")
STATUSES = ("proposed", "applied", "dismissed", "undone")
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pair": {"type": "integer"},
                    "same": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "keep": {"type": "string", "enum": ["a", "b"]},
                    "why": {"type": "string"},
                },
                "required": ["pair", "same", "confidence"],
            },
        }
    },
    "required": ["judgements"],
}
JUDGE_SYSTEM = (
    "You tidy a knowledge graph built from the text of assets (transcripts, documents, pages and more). For each numbered pair of names, say whether both names mean "
    "the same real thing (the same person, organisation, product, place, event, work or topic), how sure you are "
    "from 0 to 1, which name to keep (the fuller, correctly spelled one), and why in a few words. Say same only when "
    "the lines make it clear: two people who share a first name or a surname are not the same person. Reply with JSON "
    "only."
)
# what the model is asked about one entity: its type, or whether it's noise
ONE_SCHEMA = {
    "retype": {"type": {"type": "string"}},
    "hide": {"noise": {"type": "boolean"}},
    "cluster": {"agree": {"type": "boolean"}, "label": {"type": "string"}, "leave": {"type": "array", "items": {"type": "integer"}}},
}
ONE_SYSTEM = {
    "retype": (
        "You check the types of names in a knowledge graph built from the text of assets (transcripts, documents, "
        "pages and more). For each numbered name, say what kind of thing it is, as one of the types listed, from the "
        "lines it is said in; how sure you are from 0 to 1; and why in a few words. The rules' guess is given: keep "
        "the name's type when the lines don't show it is wrong. Reply with JSON only."
    ),
    "hide": (
        "You tidy a knowledge graph built from the text of assets (transcripts, documents, pages and more). For each "
        "numbered name, say whether it is noise: words the extractor took for a name that name nothing (a filler word, "
        "a fragment, a number, a sentence), so it should be hidden; how sure you are from 0 to 1; and why in a few "
        "words. Real people, organisations, products, places, events, works and topics are not noise, even when they "
        "are said once. Reply with JSON only."
    ),
    "cluster": (
        "You organise the vocabulary of topics a knowledge graph's assets are about. For each numbered group, say "
        "whether the topics listed go under the broader topic named (agree), as narrower kinds or parts of it; for a "
        "new broader topic, a better label for it if there is one; the numbers of the topics that don't belong "
        '(leave); how sure you are from 0 to 1; and why in a few words. Sharing a word is not enough: "Sales calls" '
        'and "Cold calls" are calls, "Judgement calls" are not. Reply with JSON only.'
    ),
}


def _one_schema(kind):
    item = {"item": {"type": "integer"}, **ONE_SCHEMA[kind], "confidence": {"type": "number"}, "why": {"type": "string"}}
    need = ["item", "agree" if kind == "cluster" else next(iter(ONE_SCHEMA[kind])), "confidence"]
    return {
        "type": "object",
        "properties": {"tidying": {"type": "array", "items": {"type": "object", "properties": item, "required": need}}},
        "required": ["tidying"],
    }


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_config(nid, t, cfg):
    """ValueError naming what is wrong with a graph node's settings."""
    from . import workflows

    if t == "candidates":
        if cfg.get("kind", "merge") not in KINDS:
            raise ValueError(f"candidates node {nid}: kind is {', '.join(KINDS[:-1])} or {KINDS[-1]}")
        mc = cfg.get("min_confidence")
        if mc is not None and (not _num(mc) or not 0 <= mc <= 1):
            raise ValueError(f"candidates node {nid}: min_confidence is between 0 and 1")
        lim = cfg.get("limit")
        if lim is not None and (not isinstance(lim, int) or not 1 <= lim <= 1000):
            raise ValueError(f"candidates node {nid}: limit is 1 to 1000 candidates")
        workflows._check_types(nid, cfg)
    elif t == "llm_judge":
        b = cfg.get("batch")
        if b is not None and (not isinstance(b, int) or not 1 <= b <= 100):
            raise ValueError(f"judge node {nid}: batch is 1 to 100 candidates")
        if cfg.get("instructions") is not None and not isinstance(cfg["instructions"], str):
            raise ValueError(f"judge node {nid}: instructions are text")
    elif t == "filter":
        workflows._check_config(None, nid, "condition", cfg)
    elif t == "apply_changes":
        a = cfg.get("apply_above")
        if a is not None and (not _num(a) or not 0 <= a <= 1):
            raise ValueError(f"apply node {nid}: apply_above is between 0 and 1, or none to only propose")
        m = cfg.get("max_apply")
        if m is not None and (not isinstance(m, int) or not 0 <= m <= 1000):
            raise ValueError(f"apply node {nid}: max_apply is 0 to 1000")


# ---------- candidates ----------
def _pair_key(kind, a, b):
    lo, hi = sorted((int(a), int(b)))
    return f"{kind}:{lo}-{hi}"


def _held(db):
    """Candidates not to suggest again: pairs said to be different, and anything with a change proposed, dismissed or
    undone (someone took it back: a run doesn't make it again)."""
    out = {_pair_key("merge", d["a"], d["b"]) for d in db.rows("SELECT a, b FROM entity_distinct")}
    out |= {_pair_key("link", d["a"], d["b"]) for d in db.rows("SELECT a, b FROM entity_distinct")}
    out |= set(db.values("SELECT VALUE pair FROM graph_change WHERE status != 'applied'"))
    return out


def _side(db, e, n, names):
    samples = [m["text"] for m in entities.mentions(db, e["id"], {e["space"]}, limit=2)["items"] if m.get("text")]
    return {
        "id": e["id"],
        "name": e["name"],
        "type": e["type"],
        "namespace": names.get(e["space"]),
        "space": e["space"],
        "mentions": n,
        "samples": samples,
    }


def candidates(db, spaces, c):
    """What may need tidying, most likely first: pairs that may be one thing [{kind, pair, a, b, reason, confidence}],
    or entities [{kind, pair, a, to (retype), reason, confidence}]."""
    kind, floor, limit = c.get("kind") or "merge", c.get("min_confidence", 0.7), c.get("limit") or 100
    types = set(c.get("types") or [])
    if kind in ("retype", "hide", "cluster"):
        return {"retype": wrong_types, "hide": noise, "cluster": topic_groups}[kind](db, spaces, floor, limit, types)
    if kind == "link":
        spaces = set(db.values("SELECT VALUE record::id(id) FROM space WHERE graph = 'shared' AND record::id(id) IN $s", s=sorted(spaces)))
    if not spaces:
        return []
    ents = [
        e
        for e in db.rows(f"SELECT {entities.FIELDS} FROM entity WHERE space IN $s", s=sorted(spaces))
        if not e.get("hidden") and not e.get("builtin") and e["type"] not in entities.QUIET and (not types or e["type"] in types)
    ]
    counts = Counter(db.values("SELECT VALUE entity FROM mentions WHERE space IN $s", s=sorted(spaces)))
    ents = [e for e in ents if counts[e["id"]]]
    info, held, found = {e["id"]: e for e in ents}, _held(db), []
    if kind == "merge":
        groups = defaultdict(list)
        for e in ents:
            groups[e["space"]].append(e)
        pairs = [p for g in groups.values() for p in entities._pairs(g).items()]
    else:
        linked = {(r["a"], r["b"]) for r in db.rows("SELECT a, b FROM entity_link")}
        pairs = [
            ((a, b), why)
            for (a, b), why in entities._pairs(ents).items()
            if info[a]["space"] != info[b]["space"] and info[a]["key"] != info[b]["key"] and (a, b) not in linked
        ]
    for (a, b), (reason, conf) in pairs:
        pk = _pair_key(kind, a, b)
        if conf >= floor and pk not in held:
            found.append((conf, counts[a] + counts[b], a, b, reason, pk))
    found.sort(key=lambda x: (-x[0], -x[1]))
    names = store.space_names(db)
    return [
        {
            "kind": kind,
            "pair": pk,
            "a": _side(db, info[a], counts[a], names),
            "b": _side(db, info[b], counts[b], names),
            "reason": reason,
            "confidence": conf,
        }
        for conf, _n, a, b, reason, pk in found[:limit]
    ]


def _live(db, sid, types):
    """A namespace's entities a run may tidy, with their mentions there: ([entity], Counter)."""
    counts = Counter(db.values("SELECT VALUE entity FROM mentions WHERE space = $s", s=sid))
    ents = [
        e
        for e in db.rows(f"SELECT {entities.FIELDS} FROM entity WHERE space = $s", s=sid)
        if counts[e["id"]]
        and not (e.get("hidden") or e.get("builtin") or e.get("defined"))
        and e["type"] not in entities.QUIET
        and (not types or e["type"] in types)
    ]
    return ents, counts


def _type_label(db, sid, typ):
    return entities.TYPES.get(typ) or entity_setup.labels(db, [sid]).get(sid, {}).get(typ) or typ.title()


def better_type(name, typ, lines, people=frozenset()):
    """(type, why, confidence) the rules read for an entity of type `typ` from its name and lines, or None when they
    don't read another one. A term the rules now type is surer than a type they would change."""
    words = name_types.WORD.findall(name) or [name]
    own = name_types.kind(words, people=people)  # what the name says on its own: a title, an ending, a known name
    counts, said = name_types.votes(name, lines, people)
    top = name_types.best(counts)
    term = typ == "TERM"
    if own and own != typ and top in (None, own):
        return own, "its name says so", 0.9 if term else 0.8
    if top and top != typ and said >= 2 and counts[top] * 2 >= said:
        share = counts[top] / said
        return top, f"{counts[top]} of {said} lines say so", round(min(0.9, 0.5 + 0.4 * share) if term else min(0.8, 0.4 + 0.4 * share), 2)
    return None


def wrong_types(db, spaces, floor=0.7, limit=100, types=()):
    """Entities whose type the rules read differently now: [{kind: retype, pair, a, to, reason, confidence}]."""
    held, people_set, names, found = _held(db), entities.typed_by_people(db), store.space_names(db), []
    for sid in sorted(spaces):
        ents, counts = _live(db, sid, set(types))
        ents = [e for e in ents if e["id"] not in people_set]
        if not ents:
            continue
        codes, setup, people = entity_setup.type_codes(db, sid), entity_setup.effective(db, sid), analyze.people_of(db, sid)
        for k in range(0, len(ents), entities.TYPED_CHUNK):
            part = ents[k : k + entities.TYPED_CHUNK]
            lines = entities._lines_of(db, [e["id"] for e in part])
            for e in part:
                got = better_type(e["name"], e["type"], lines.get(e["id"]) or [], people)
                if not got or got[0] not in codes or not entity_setup.keeps(setup, got[0]):
                    continue
                to, why, conf = got
                pk = f"retype:{e['id']}:{to}"
                if conf >= floor and pk not in held:
                    found.append((conf, counts[e["id"]], e, to, f"{_type_label(db, sid, to)}: {why}", pk))
    found.sort(key=lambda x: (-x[0], -x[1]))
    return [
        {"kind": "retype", "pair": pk, "a": _side(db, e, n, names), "to": to, "reason": why, "confidence": conf}
        for conf, n, e, to, why, pk in found[:limit]
    ]


FILLER = {w.casefold() for w in analyze.STOP | analyze.STOPCAP}
PHRASE_WORDS, PHRASE_CHARS = 6, 60  # longer than this reads like a phrase, not a name


def noise_reason(name, typ, mentions):
    """(why, confidence) a name looks like noise, or None."""
    words = analyze.WORD.findall(name)
    term = typ == "TERM"
    if not re.search(r"[^\W\d_]", name):
        return "no letters in it", 0.95
    if len(name.strip()) == 1:
        return "a single letter", 0.95
    if all(w.casefold() in FILLER for w in words) and not (name.isupper() and len(name) > 1):  # US, OK are names
        return "filler words, not a name", 0.95 if term else 0.7
    if len(words) > PHRASE_WORDS or len(name) > PHRASE_CHARS:
        return "reads like a phrase, not a name", 0.8
    if term and mentions == 1:
        return "a term said only once", 0.5
    return None


def _shown_again(db):
    """Entities someone showed after they were hidden."""
    out = set()
    for ev in db.rows("SELECT ops FROM graph_event WHERE op = 'entity.show'"):
        out.update(int(o["k"]) for o in ev.get("ops") or [] if o.get("t") == "entity" and str(o.get("k")).isdigit())
    return out


def noise(db, spaces, floor=0.7, limit=100, types=()):
    """Entities that look like noise, to hide: [{kind: hide, pair, a, reason, confidence}]."""
    held, shown, names, found = _held(db), _shown_again(db), store.space_names(db), []
    for sid in sorted(spaces):
        ents, counts = _live(db, sid, set(types))
        for e in ents:
            got = noise_reason(e["name"], e["type"], counts[e["id"]])
            pk = f"hide:{e['id']}"
            if got and got[1] >= floor and pk not in held and e["id"] not in shown:
                found.append((got[1], -counts[e["id"]], e, counts[e["id"]], got[0], pk))
    found.sort(key=lambda x: (-x[0], -x[1]))
    return [
        {"kind": "hide", "pair": pk, "a": _side(db, e, n, names), "reason": why, "confidence": conf}
        for conf, _o, e, n, why, pk in found[:limit]
    ]


GROUP_MIN = 3  # topics that share a last word before they're grouped under a new topic for it


def _words(label):
    return [w.casefold() for w in analyze.WORD.findall(label or "")]


def topic_groups(db, spaces, floor=0.7, limit=100, types=()):
    """Topics to group under a broader topic: [{kind: cluster, pair, a: the broader topic {id, name}, topics, reason,
    confidence}]. Under an existing topic when their labels end in its label; under a new one named for their last
    word when GROUP_MIN or more topics with no broader one end in it."""
    held, names, found = _held(db), store.space_names(db), []
    for sid in sorted(spaces):
        rows = db.rows("SELECT record::id(id) AS id, key, label, broader FROM topic WHERE space = $s", s=sid)
        if len(rows) < 2:
            continue
        uses = Counter(db.values("SELECT VALUE topic FROM topic_about WHERE space = $s AND status = 'accepted'", s=sid))
        up = {t["id"]: set(t.get("broader") or []) for t in rows}
        by_key = {t["key"]: t for t in rows}

        def above(tid, seen=None):
            seen = seen if seen is not None else set()
            for b in up.get(tid, ()):
                if b not in seen:
                    seen.add(b)
                    above(b, seen)
            return seen

        groups = defaultdict(list)  # (parent key, label, existing id) -> children
        for t in rows:
            ws = _words(t["label"])
            for n in range(len(ws) - 1, 0, -1):  # the longest label it ends in that is a topic: "deep learning" > "learning"
                p = by_key.get(topics.key_of(" ".join(ws[-n:])))
                if p and p["id"] != t["id"]:
                    if p["id"] not in up[t["id"]] and t["id"] not in above(p["id"]):
                        groups[(p["key"], p["label"], p["id"])].append(t)
                    break
            else:
                if len(ws) > 1 and not up[t["id"]] and len(ws[-1]) >= 4 and ws[-1] not in FILLER and topics.key_of(ws[-1]) not in by_key:
                    groups[(topics.key_of(ws[-1]), ws[-1], None)].append(t)
        for (key, label, pid), kids in groups.items():
            if pid is None:
                if len(kids) < GROUP_MIN:
                    continue
                label = Counter(analyze.WORD.findall(k["label"])[-1] for k in kids).most_common(1)[0][0]
                label = label[:1].upper() + label[1:]
                reason, conf = f"{len(kids)} topics end in “{label.casefold()}”", round(min(0.85, 0.55 + 0.05 * len(kids)), 2)
            else:
                reason, conf = f"their labels end in “{label}”", 0.85
            pk = f"cluster:{sid}:{key}"
            if conf >= floor and pk not in held:
                kids = sorted(kids, key=lambda k: (-uses[k["id"]], k["label"].casefold()))
                found.append((conf, sum(uses[k["id"]] for k in kids), sid, pid, label, kids, reason, pk))
    found.sort(key=lambda x: (-x[0], -x[1]))
    return [
        {
            "kind": "cluster",
            "pair": pk,
            "a": {"id": pid, "name": label, "type": "TOPIC", "namespace": names.get(sid), "space": sid},
            "topics": [{"id": k["id"], "name": k["label"], "recordings": uses[k["id"]]} for k in kids],
            "reason": reason,
            "confidence": conf,
        }
        for conf, _n, sid, pid, label, kids, reason, pk in found[:limit]
    ]


# ---------- the model's verdict ----------
def _describe(k, item):
    def side(s):
        said = "; ".join(f'"{x[:160]}"' for x in s["samples"]) or "no lines"
        return f'"{s["name"]}" ({s["type"]}, {s["mentions"]} mentions in {s["namespace"]}; found in: {said})'

    return f"{k}. a: {side(item['a'])}\n   b: {side(item['b'])}\n   rules thought: {item['reason']} ({item['confidence']})"


def _pairs_judged(cfg, batch, c):
    """{number in the batch: verdict} for pairs: one thing or two, and which name to keep."""
    ask = [c["instructions"].strip()] if c.get("instructions") else []
    ask.append("Pairs:\n" + "\n".join(_describe(k, it) for k, it in enumerate(batch, 1)))
    reply = llm.json_out(cfg, JUDGE_SYSTEM, "\n\n".join(ask), JUDGE_SCHEMA, c.get("model"))
    got = {}
    for d in (reply or {}).get("judgements") or []:
        if isinstance(d, dict) and isinstance(d.get("pair"), int) and isinstance(d.get("same"), bool):
            got[d["pair"]] = store.clean(
                {
                    "same": d["same"],
                    "agree": d["same"],
                    "confidence": _sure(d.get("confidence")),
                    "keep": d.get("keep") if d.get("keep") in ("a", "b") else None,
                    "why": str(d.get("why") or "")[:300] or None,
                }
            )
    return got


def _sure(v):
    return round(min(1.0, max(0.0, float(v))), 2) if _num(v) else 0


def _describe_one(k, item):
    if item["kind"] == "cluster":
        a, kids = item["a"], "\n".join(f"   {n}. {t['name']}" for n, t in enumerate(item["topics"], 1))
        under = f'"{a["name"]}" (a topic already)' if a.get("id") else f'"{a["name"]}" (a new topic)'
        return f"{k}. under {under}, in {a['namespace']}:\n{kids}\n   rules think: group ({item['reason']}; {item['confidence']})"
    s = item["a"]
    said = "; ".join(f'"{x[:160]}"' for x in s["samples"]) or "no lines"
    line = f'{k}. "{s["name"]}" ({s["type"]}, {s["mentions"]} mentions in {s["namespace"]}; found in: {said})'
    if item["kind"] == "retype":
        return f"{line}\n   rules think: {item['to']} ({item['reason']}; {item['confidence']})"
    return f"{line}\n   rules think: noise ({item['reason']}; {item['confidence']})"


def _ones_judged(db, cfg, kind, batch, c):
    """{number in the batch: verdict} for entities: their type, or whether they are noise."""
    ask = [c["instructions"].strip()] if c.get("instructions") else []
    codes = set()
    if kind == "retype":
        for sid in {it["a"]["space"] for it in batch}:
            codes |= entity_setup.type_codes(db, sid) - set(entities.QUIET)
        ask.append("Types: " + ", ".join(sorted(codes)))
    ask.append(("Groups" if kind == "cluster" else "Names") + ":\n" + "\n".join(_describe_one(k, it) for k, it in enumerate(batch, 1)))
    reply = llm.json_out(cfg, ONE_SYSTEM[kind], "\n\n".join(ask), _one_schema(kind), c.get("model"))
    got = {}
    for d in (reply or {}).get("tidying") or []:
        if not (isinstance(d, dict) and isinstance(d.get("item"), int) and 1 <= d["item"] <= len(batch)):
            continue
        it, why = batch[d["item"] - 1], str(d.get("why") or "")[:300] or None
        if kind == "retype":
            typ = str(d.get("type") or "").strip().upper()
            if typ not in codes:
                continue
            got[d["item"]] = store.clean(
                {"agree": typ != it["a"]["type"], "type": typ, "confidence": _sure(d.get("confidence")), "why": why}
            )
        elif kind == "cluster":
            if not isinstance(d.get("agree"), bool):
                continue
            leave = sorted({int(n) for n in d.get("leave") or [] if isinstance(n, int) and 1 <= n <= len(it["topics"])})
            label = " ".join(str(d.get("label") or "").split())[:200]
            got[d["item"]] = store.clean(
                {
                    "agree": d["agree"] and len(leave) < len(it["topics"]),
                    "label": label if label and not it["a"].get("id") and label.casefold() != it["a"]["name"].casefold() else None,
                    "leave": [it["topics"][n - 1]["id"] for n in leave] or None,
                    "confidence": _sure(d.get("confidence")),
                    "why": why,
                }
            )
        elif isinstance(d.get("noise"), bool):
            got[d["item"]] = store.clean({"agree": d["noise"], "confidence": _sure(d.get("confidence")), "why": why})
    return got


def judge(cfg, items, c, say=print, db=None):
    """The items, each with the model's `verdict` (those it gave no answer for get none). Each kind is asked on its own,
    a batch at a time."""
    size, verdicts = c.get("batch") or 25, {}
    by_kind = defaultdict(list)
    for n, it in enumerate(items):
        if isinstance(it, dict) and it.get("kind") in KINDS and isinstance(it.get("a"), dict):
            by_kind["pair" if it["kind"] in PAIRS else it["kind"]].append(n)
    for kind, ns in by_kind.items():
        what = {"pair": "pairs", "cluster": "groups"}.get(kind, "names")
        for start in range(0, len(ns), size):
            part = ns[start : start + size]
            batch = [items[n] for n in part]
            try:
                got = _pairs_judged(cfg, batch, c) if kind == "pair" else _ones_judged(db, cfg, kind, batch, c)
            except Exception as e:  # noqa: BLE001 - one bad batch leaves its candidates unjudged, not the run failed
                say(f"{what} {start + 1}-{start + len(batch)}: the model failed ({type(e).__name__}: {str(e)[:200]})")
                got = {}
            verdicts.update({part[k - 1]: v for k, v in got.items() if 1 <= k <= len(part)})
            say(f"judged {len(got)} of {len(batch)} {what}")
    return [{**it, "verdict": verdicts[n]} if n in verdicts else it for n, it in enumerate(items)]


# ---------- changes ----------
def _alive(db, eid):
    return bool(db.one("SELECT id FROM $r", r=R("entity", int(eid))))


def _keep(item):
    v = item.get("verdict") or {}
    if v.get("keep") in ("a", "b"):
        return item[v["keep"]]["id"]
    return item["a"]["id"] if item["a"]["mentions"] >= item["b"]["mentions"] else item["b"]["id"]


def _make(db, ch, user):
    """Make one change; the fields to record on it (merge id), or None when it can't be made any more."""
    if ch["kind"] == "cluster":
        return _make_group(db, ch, user)
    if ch["kind"] not in PAIRS:
        return _make_one(db, ch)
    a, b = ch["a"]["id"], ch["b"]["id"]
    if not (_alive(db, a) and _alive(db, b)):
        return None
    if ch["kind"] == "merge":
        keep = ch.get("keep") or a
        return {"merge": entities.merge(db, keep, [b if keep == a else a], user=user), "keep": keep}
    entities.link(db, a, b, check_spaces=False)
    return {}


def _make_group(db, ch, user):
    """Put a group's topics under its broader topic, made first when it's new: {parent, created, grouped}, or None when
    none of them can go under it any more (gone, or it would loop)."""
    v, a, sid = ch.get("verdict") or {}, ch["a"], ch["a"]["space"]
    pid = a.get("id") if a.get("id") and db.one("SELECT id FROM $r", r=R("topic", int(a["id"]))) else None
    label = ch.get("label") or v.get("label") or a["name"]
    if pid is None:
        got = db.one("SELECT record::id(id) AS id FROM topic WHERE tkey = $k", k=f"{int(sid)}:{topics.key_of(label)}")
        pid = got["id"] if got else None
    created = None
    if pid is None:
        try:
            pid = created = topics.create(db, sid, label, user=user)
        except ValueError:  # another topic has that label as one of its others
            return None
    leave, grouped = set(v.get("leave") or []), []
    for t in ch.get("topics") or []:
        row = db.one("SELECT broader FROM $r", r=R("topic", int(t["id"])))
        if t["id"] in leave or t["id"] == pid or not row or pid in (row.get("broader") or []):
            continue
        try:
            topics.update(db, t["id"], broader=[*(row.get("broader") or []), pid], user=user)
        except (ValueError, KeyError):  # it would loop, or the topic has as many broader ones as it can
            continue
        grouped.append(t["id"])
    if not grouped:
        if created:
            _drop_unused_topic(db, created)
        return None
    return {"parent": pid, "created": created, "grouped": grouped}


def _drop_unused_topic(db, tid):
    """Delete a topic a group made, unless something has used it since (a recording, a narrower or related topic)."""
    t = db.one("SELECT space, related FROM $r", r=R("topic", int(tid)))
    if not t or t.get("related") or db.one("SELECT id FROM topic_about WHERE topic = $t LIMIT 1", t=int(tid)):
        return False
    if db.one("SELECT id FROM topic WHERE space = $s AND $t IN broader LIMIT 1", s=t["space"], t=int(tid)):
        return False
    db.q("DELETE $r", r=R("topic", int(tid)))
    notebook.release(db, f"topic:{int(tid)}")
    return True


def _make_one(db, ch):
    """Retype or hide one entity, unless it's gone or has changed since the change was found."""
    e = db.one(f"SELECT {entities.FIELDS} FROM $r", r=R("entity", int(ch["a"]["id"])))
    if not e or e.get("hidden"):
        return None
    if ch["kind"] == "retype":
        to = ch.get("to")
        if e["type"] != ch["a"]["type"] or not to or to == e["type"]:
            return None
        try:
            entities.retype(db, [e["id"]], to)
        except ValueError:  # the type is gone from the namespace, or the entity is on a fixed list now
            return None
        return {"to": to}
    entities.hide(db, e["id"], True, f"noise: {ch.get('reason') or 'tidied'}")
    return {}


def _target(it):
    """A retype's type: the model's, when it gave one, else the rules'."""
    v = it.get("verdict") or {}
    return v.get("type") or it.get("to")


def _touches(it):
    if it["kind"] == "cluster":  # topics, not entities: not in the graph's history
        return []
    return [it["a"]["id"], *([it["b"]["id"]] if it["kind"] in PAIRS else [])]


def _record(db, item, status, origin, extra=None):
    cid = db.next_id("graph_change")  # a new id also tells the graph cache to rebuild
    row = store.clean(
        {
            "kind": item["kind"],
            "pair": item["pair"],
            "a": item["a"],
            "b": item.get("b"),
            "to": _target(item) if item["kind"] == "retype" else None,
            "topics": item.get("topics") if item["kind"] == "cluster" else None,
            "spaces": sorted({item["a"]["space"], *([item["b"]["space"]] if item.get("b") else [])}),
            "reason": item.get("reason"),
            "confidence": item.get("confidence"),
            "verdict": item.get("verdict"),
            "keep": _keep(item) if item["kind"] == "merge" else None,
            "status": status,
            "created_at": store.now(),
            **(origin or {}),
            **(extra or {}),
        }
    )
    db.q("CREATE $r CONTENT $d", r=R("graph_change", cid), d=row)
    return cid


def apply_changes(db, items, c, origin=None, propose_only=False, say=print):
    """Make the sure ones (up to max_apply), propose the rest: {applied, proposed, skipped}."""
    above, cap = c.get("apply_above"), c.get("max_apply", 50)
    stats = {"applied": 0, "proposed": 0, "skipped": 0}
    user = f"routine:{origin['routine']}" if origin and origin.get("routine") else "workflow"
    for it in items if isinstance(items, list) else []:
        if not (
            isinstance(it, dict)
            and it.get("kind") in KINDS
            and isinstance(it.get("a"), dict)
            and (it["kind"] not in PAIRS or isinstance(it.get("b"), dict))
            and (it["kind"] != "retype" or _target(it))
            and (it["kind"] != "cluster" or isinstance(it.get("topics"), list))
        ):
            stats["skipped"] += 1
            continue
        v = it.get("verdict") or {}
        if v.get("same") is False or v.get("agree") is False:
            stats["skipped"] += 1
            continue
        sure = v.get("confidence", it.get("confidence") or 0)
        if not propose_only and above is not None and sure >= above and stats["applied"] < cap:
            why = v.get("why") or it.get("reason")
            with graph_history.change(db, f"{it['kind']}.apply", entities=_touches(it), why=why) as ch:
                done = _make(db, {**it, "keep": _keep(it) if it["kind"] == "merge" else None, "to": _target(it)}, user)
                if done is not None:
                    ch.add(graph_change=_record(db, it, "applied", origin, {**done, "decided_at": store.now(), "decided_by": user}))
            if done is None:
                stats["skipped"] += 1
                continue
            stats["applied"] += 1
        else:
            if db.one("SELECT id FROM graph_change WHERE pair = $p AND status = 'proposed' LIMIT 1", p=it["pair"]):
                stats["skipped"] += 1
                continue
            _record(db, it, "proposed", origin)
            stats["proposed"] += 1
    say(f"{stats['applied']} applied, {stats['proposed']} proposed, {stats['skipped']} skipped")
    return stats


def propose(db, kind, a, b, reason=None, origin=None, apply=False, user=None):
    """One change someone or an agent asked for (a merge inside a namespace, a link across namespaces): proposed for
    a person to accept, or made at once when `apply` (callers check the rights). Returns (change id, status)."""
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    x, y = (entities._entity(db, int(e)) for e in (a, b))
    if x["id"] == y["id"]:
        raise ValueError("pick two different entities")
    if kind == "merge" and x["space"] != y["space"]:
        raise ValueError("these are in different namespaces: link them instead")
    if kind == "link" and x["space"] == y["space"]:
        raise ValueError("these are in the same namespace: merge them instead")
    pk = _pair_key(kind, x["id"], y["id"])
    held = db.one("SELECT record::id(id) AS id FROM graph_change WHERE pair = $p AND status = 'proposed' LIMIT 1", p=pk)
    if held and not apply:
        return held["id"], "proposed"
    names, counts = store.space_names(db), Counter(db.values("SELECT VALUE entity FROM mentions WHERE entity IN $e", e=[x["id"], y["id"]]))
    item = {
        "kind": kind,
        "pair": pk,
        "a": _side(db, x, counts[x["id"]], names),
        "b": _side(db, y, counts[y["id"]], names),
        "reason": (str(reason)[:300] if reason else None) or "asked for",
        "confidence": None,
    }
    if not apply:
        return _record(db, item, "proposed", origin), "proposed"
    with graph_history.change(db, f"{kind}.apply", entities=[x["id"], y["id"]], why=item["reason"]) as ch:
        done = _make(db, {**item, "keep": x["id"] if kind == "merge" else None}, user)
        if held:
            db.q(
                "UPDATE $r SET status = 'dismissed', decided_at = $t, decided_by = $u",
                r=R("graph_change", held["id"]),
                t=store.now(),
                u=user,
            )
        cid = _record(
            db,
            {**item},
            "applied",
            origin,
            {**(done or {}), "keep": x["id"] if kind == "merge" else None, "decided_at": store.now(), "decided_by": user},
        )
        ch.add(graph_change=cid)
    return cid, "applied"


def get_change(db, cid):
    ch = db.one("SELECT *, record::id(id) AS id FROM $r", r=R("graph_change", int(cid)))
    if not ch:
        raise KeyError(cid)
    return ch


def accept(db, cid, user=None, keep=None, to=None, label=None):
    """Make a proposed change (`keep`: a merge's entity to keep; `to`: a retype's type, in place of the one proposed;
    `label`: a group's new broader topic's label)."""
    ch = get_change(db, cid)
    if ch["status"] != "proposed":
        raise ValueError(f"this change is {ch['status']}, not proposed")
    if keep is not None:
        if ch["kind"] != "merge" or int(keep) not in (ch["a"]["id"], ch["b"]["id"]):
            raise ValueError("keep one of the two entities")
        ch["keep"] = int(keep)
    if to is not None:
        if ch["kind"] != "retype":
            raise ValueError("only a change of type takes a type")
        entities._check_type(db, ch["a"]["space"], to)
        ch["to"] = to
    if label is not None:
        if ch["kind"] != "cluster" or ch["a"].get("id"):
            raise ValueError("only a group under a new topic takes a label")
        ch["label"] = " ".join(str(label).split()) or None
    with graph_history.change(db, f"{ch['kind']}.accept", entities=_touches(ch), why=ch.get("reason"), graph_change=ch["id"]):
        done = _make(db, ch, user)
    if done is None:
        db.q(
            "UPDATE $r SET status = 'dismissed', decided_at = $t, decided_by = $u, note = 'an entity is gone'",
            r=R("graph_change", ch["id"]),
            t=store.now(),
            u=user,
        )
        if ch["kind"] in PAIRS:
            raise ValueError("one of these entities is gone (merged or deleted since)")
        if ch["kind"] == "cluster":
            raise ValueError("none of these topics can go under it now (gone, under it already, or it would loop)")
        raise ValueError("the entity is gone, hidden or changed since this was proposed")
    db.q(
        "UPDATE $r MERGE $d",
        r=R("graph_change", ch["id"]),
        d=store.clean({"status": "applied", "decided_at": store.now(), "decided_by": user, **done}),
    )


def dismiss(db, cid, user=None):
    """Say a proposed pair is two things, so it isn't suggested again."""
    ch = get_change(db, cid)
    if ch["status"] != "proposed":
        raise ValueError(f"this change is {ch['status']}, not proposed")
    if ch["kind"] in PAIRS and _alive(db, ch["a"]["id"]) and _alive(db, ch["b"]["id"]):
        with graph_history.change(db, f"{ch['kind']}.dismiss", graph_change=ch["id"]):
            entities.not_same(db, ch["a"]["id"], ch["b"]["id"])
    db.q("UPDATE $r SET status = 'dismissed', decided_at = $t, decided_by = $u", r=R("graph_change", ch["id"]), t=store.now(), u=user)


def undo(db, cid, user=None):
    """Take back an applied change (a merge is undone as the entity page would). A type or a hide that someone has
    changed since stays as they left it."""
    ch = get_change(db, cid)
    if ch["status"] != "applied":
        raise ValueError(f"this change is {ch['status']}, not applied")
    one = [ch["a"]["id"]] if ch["kind"] in ("retype", "hide") else []
    with graph_history.change(db, f"{ch['kind']}.undo", entities=one, graph_change=ch["id"]):
        if ch["kind"] == "merge":
            m = db.one("SELECT undone FROM $r", r=R("entity_merge", int(ch["merge"])))
            if m and not m.get("undone"):  # someone may have undone it from the entity page already
                entities.undo_merge(db, ch["merge"])
        elif ch["kind"] == "link":
            entities.unlink(db, ch["a"]["id"], ch["b"]["id"])
        elif ch["kind"] == "cluster":
            _ungroup(db, ch)
        else:
            e = db.one(f"SELECT {entities.FIELDS} FROM $r", r=R("entity", int(ch["a"]["id"])))
            if not e:
                raise ValueError("the entity is gone (merged or deleted since)")
            if ch["kind"] == "retype" and e["type"] == ch.get("to") and not e.get("builtin"):
                db.q("UPDATE $r SET type = $t", r=R("entity", e["id"]), t=ch["a"]["type"])
            elif ch["kind"] == "hide" and e.get("hidden") and str(e.get("hidden_reason") or "").startswith("noise:"):
                entities.hide(db, e["id"], False)
    db.q("UPDATE $r SET status = 'undone', undone_at = $t, undone_by = $u", r=R("graph_change", ch["id"]), t=store.now(), u=user)


def _ungroup(db, ch):
    """Take a group's topics out from under its broader topic (those someone hasn't moved since), and delete the broader
    topic when the group made it and nothing uses it."""
    pid = ch.get("parent")
    for tid in ch.get("grouped") or []:
        row = db.one("SELECT broader FROM $r", r=R("topic", int(tid)))
        if row and pid in (row.get("broader") or []):
            db.q(
                "UPDATE $r SET broader = $b, updated = $t", r=R("topic", int(tid)), b=[b for b in row["broader"] if b != pid], t=store.now()
            )
    if ch.get("created"):
        _drop_unused_topic(db, ch["created"])


def undo_run(db, run_id, user=None, say=None):
    """Take back every change a routine run applied, newest first: (undone, failed). One that can't be undone (an
    entity in it was deleted since) is left as it is and counted."""
    rows = db.rows("SELECT record::id(id) AS id FROM graph_change WHERE run = $r AND status = 'applied' ORDER BY id DESC", r=int(run_id))
    done = failed = 0
    for r in rows:
        try:
            undo(db, r["id"], user)
            done += 1
        except Exception as e:  # noqa: BLE001 - the rest still get undone
            failed += 1
            if say:
                say(f"change {r['id']}: {type(e).__name__}: {e}")
    return done, failed


def list_changes(db, spaces, status=None, run=None, limit=200):
    where, args = ["spaces ANYINSIDE $s"], {"s": sorted(spaces)}
    if status:
        where.append("status = $st")
        args["st"] = status
    if run is not None:
        where.append("run = $rn")
        args["rn"] = int(run)
    rows = db.rows(
        "SELECT *, record::id(id) AS id FROM graph_change WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT $n",
        n=int(limit),
        **args,
    )
    return [r for r in rows if set(r.get("spaces") or []) <= set(spaces)]  # all of a change's namespaces must be yours


# ---------- running a graph workflow ----------
def run(db, cfg, wid, spaces, version=None, say=print, origin=None, propose_only=False):
    """Run one version (default: the current one) of a graph workflow over namespaces `spaces`. Returns
    ({node id: done | skipped}, {applied, proposed, skipped}, with `published` and `episodes` when its nodes publish
    (feed_nodes.py))."""
    from . import flow, workflows

    w = workflows.get(db, wid, version)
    if w.get("scope") != "graph":
        raise ValueError(f"workflow {w['name']} runs on assets, not on the graph")
    names = store.space_names(db)
    ctx = {"namespaces": [{"id": s, "name": names.get(s)} for s in sorted(spaces)]}
    origin = {**(origin or {}), "workflow": w["id"], "workflow_version": w["version"]}
    r = flow.Run(db, cfg, workflows.KITS["graph"], ctx, say, origin=origin, spaces=set(spaces), propose_only=propose_only)
    r.totals = {"applied": 0, "proposed": 0, "skipped": 0}
    routine = origin.get("routine")
    keep = {k: origin[k] for k in ("routine", "run", "workflow", "workflow_version") if origin.get(k) is not None}
    with graph_history.acting(actor=f"routine:{routine}" if routine else None, via="routine" if routine else "workflow", **keep):
        flow.run_graph(r, w["graph"])
    done = {n["id"]: "done" if r.trace.get(n["id"], {}).get("status") == "done" else "skipped" for n in w["graph"]["nodes"]}
    return done, r.totals


def run_node(r, f, node, value, vals):
    """What a graph workflow's own nodes do (flow.py runs the rest): {output port: value}."""
    t, c, name = node["type"], node["config"], node.get("label") or f"{node['type']} {node['id']}"
    if t == "candidates":
        out = candidates(r.db, r.extra["spaces"], c)
        r.say(f"{name}: {len(out)} {'pairs' if (c.get('kind') or 'merge') in PAIRS else 'found'}")
        return {"out": out}
    if t == "llm_judge":
        return {"out": judge(r.cfg, value if isinstance(value, list) else [], c, r.say, r.db)}
    if t == "apply_changes":
        if r.dry:
            r.say(f"{name}: would make or propose {len(value) if isinstance(value, list) else 0} changes")
            return {}
        got = apply_changes(r.db, value, c, r.origin, r.extra.get("propose_only"), r.say)
        r.totals = {k: r.totals.get(k, 0) + got[k] for k in ("applied", "proposed", "skipped")}
    return {}


# ---------- the built-in workflow ----------
DEFAULT_NAME = "Organise the entity graph"
DEFAULT_GRAPH = {
    "nodes": [
        {"id": "in", "type": "input", "config": {}, "x": 0, "y": 120},
        {
            "id": "same_ns",
            "type": "candidates",
            "config": {"kind": "merge", "min_confidence": 0.7, "limit": 100},
            "label": "Look-alikes in a namespace",
            "x": 240,
            "y": 40,
        },
        {
            "id": "across",
            "type": "candidates",
            "config": {"kind": "link", "min_confidence": 0.85, "limit": 50},
            "label": "Look-alikes across namespaces",
            "x": 240,
            "y": 200,
        },
        {"id": "all", "type": "merge", "config": {}, "x": 480, "y": 120},
        {"id": "judge", "type": "llm_judge", "config": {"batch": 25}, "label": "Ask the model", "x": 680, "y": 120},
        {
            "id": "same",
            "type": "filter",
            "config": {"path": "verdict.same", "op": "equals", "value": True},
            "label": "Same thing",
            "x": 900,
            "y": 120,
        },
        {
            "id": "apply",
            "type": "apply_changes",
            "config": {"apply_above": 0.95, "max_apply": 25},
            "label": "Merge the sure ones, propose the rest",
            "x": 1120,
            "y": 120,
        },
    ],
    "edges": [
        {"source": "in", "target": "same_ns"},
        {"source": "in", "target": "across"},
        {"source": "same_ns", "target": "all"},
        {"source": "across", "target": "all"},
        {"source": "all", "target": "judge"},
        {"source": "judge", "target": "same"},
        {"source": "same", "target": "apply"},
    ],
}
DEFAULT_DESCRIPTION = (
    "Finds entities that look alike, in each namespace and across shared ones, asks the model whether each pair is "
    "one thing, merges or links the pairs it is sure of (95% or more, 25 a run) and proposes the rest. Every change "
    "can be undone."
)
