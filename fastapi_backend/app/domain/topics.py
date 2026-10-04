"""Topics: each namespace's controlled vocabulary of what its recordings are about, apart from its entities.

Entities are the named things a transcript mentions (people, organisations, places...). A topic is a subject someone
chose for the vocabulary, as a SKOS concept: a preferred label, other labels it goes by, a definition, broader and
related topics. Narrower topics are the ones that name it as broader. Recordings are about topics (`topic_about`),
put there by a person, by turning an entity of type TERM into a topic, or (suggested) by analysis.

    topic        {space, key, tkey: "<space>:<key>", label, alt: [labels], definition, broader: [ids], related: [ids],
                  origin: {entity}, created, updated, by}
    topic_about  topic_about:⟨"<recording>-<topic>"⟩ {space, recording, topic, source, weight, status, at, by}

`source` says how a recording came to be about a topic (person, entity, analysis) and `status` whether it holds
(accepted), waits for someone to accept it (suggested) or was turned down (dismissed: analysis won't suggest it again).
Analysis suggests the vocabulary's topics for recordings whose summary or transcript says one of their labels
(`suggest`), and lists what summaries talk about that the vocabulary lacks (`candidates`), for people to add or skip. Labels are unique within a namespace, alternative labels too.
A topic made from an entity hides that entity; deleting the topic shows it again. Callers check roles and write the
audit log.
"""

from __future__ import annotations

from collections import Counter

from . import analyze, store

R = store.R
FIELDS = "record::id(id) AS id, space, key, label, alt, definition, broader, related, origin, created, updated, by"
LABEL_MAX = 200
ALT_MAX = 50
DEFINITION_MAX = 2000
LINKS_MAX = 50
SOURCES = ("person", "entity", "analysis")
STATUSES = ("accepted", "suggested", "dismissed")
SUGGEST_MIN = 2  # times a transcript says a label before analysis suggests its topic
SUMMARY_WEIGHT = 5  # what a summary naming a topic adds to its weight
CANDIDATES_MAX = 30


def key_of(label):
    return analyze.ent_key(" ".join(str(label or "").split()))


def _topic(db, tid):
    t = db.one(f"SELECT {FIELDS} FROM $r", r=R("topic", int(tid)))
    if not t:
        raise KeyError(tid)
    return t


def _about_id(rid, tid):
    return R("topic_about", f"{int(rid)}-{int(tid)}")


def _clean_label(label):
    label = " ".join(str(label or "").split())
    if not label:
        raise ValueError("Give the topic a label.")
    if len(label) > LABEL_MAX:
        raise ValueError(f"A label can have up to {LABEL_MAX} characters.")
    return label


def _clean_alt(alt, label):
    out, seen = [], {key_of(label)}
    for a in alt or []:
        a = " ".join(str(a or "").split())
        k = key_of(a)
        if not k or k in seen:
            continue
        if len(a) > LABEL_MAX:
            raise ValueError(f"A label can have up to {LABEL_MAX} characters.")
        seen.add(k)
        out.append(a)
    if len(out) > ALT_MAX:
        raise ValueError(f"A topic can have up to {ALT_MAX} other labels.")
    return out


def _labels_taken(db, sid, keys, but=None):
    """The first of `keys` another topic of the namespace already goes by, or None."""
    for t in db.rows("SELECT record::id(id) AS id, key, alt FROM topic WHERE space = $s", s=int(sid)):
        if t["id"] == but:
            continue
        theirs = {t["key"]} | {key_of(a) for a in t.get("alt") or []}
        for k in keys:
            if k in theirs:
                return k
    return None


def _links(db, sid, ids, own=None, what="broader"):
    ids = list(dict.fromkeys(int(i) for i in ids or []))
    if own is not None and own in ids:
        raise ValueError(f"A topic can't be {what} than itself.")
    if len(ids) > LINKS_MAX:
        raise ValueError(f"A topic can have up to {LINKS_MAX} {what} topics.")
    if ids:
        found = {
            t["id"]: t["space"]
            for t in db.rows("SELECT record::id(id) AS id, space FROM topic WHERE id IN $ids", ids=[R("topic", i) for i in ids])
        }
        for i in ids:
            if i not in found:
                raise KeyError(i)
            if found[i] != int(sid):
                raise ValueError("Broader and related topics are in the same namespace.")
    return ids


def _broader_map(db, sid):
    return {
        t["id"]: list(t.get("broader") or [])
        for t in db.rows("SELECT record::id(id) AS id, broader FROM topic WHERE space = $s", s=int(sid))
    }


def _would_loop(db, sid, tid, broader):
    """True when making `broader` the broader topics of `tid` would make it broader than itself."""
    up = _broader_map(db, sid)
    up[tid] = list(broader)
    seen, todo = set(), list(broader)
    while todo:
        x = todo.pop()
        if x == tid:
            return True
        if x not in seen:
            seen.add(x)
            todo.extend(up.get(x, []))
    return False


def _set_related(db, tid, before, after):
    """Related is symmetric: keep the other side in step."""
    for o in set(after) - set(before):
        db.q("UPDATE $r SET related = array::union(related ?? [], [$t])", r=R("topic", o), t=tid)
    for o in set(before) - set(after):
        db.q("UPDATE $r SET related = array::complement(related ?? [], [$t])", r=R("topic", o), t=tid)


def create(db, sid, label, alt=(), definition=None, broader=(), related=(), origin=None, user=None):
    """A new topic in namespace `sid`; its id."""
    label = _clean_label(label)
    alt = _clean_alt(alt, label)
    key = key_of(label)
    taken = _labels_taken(db, sid, [key] + [key_of(a) for a in alt])
    if taken:
        raise ValueError(f"There is already a topic called {taken}.")
    definition = _definition(definition)
    broader = _links(db, sid, broader)
    related = _links(db, sid, related, what="related")
    tid = db.next_id("topic")
    at = store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("topic", tid),
        d=store.clean(
            {
                "space": int(sid),
                "key": key,
                "tkey": f"{int(sid)}:{key}",
                "label": label,
                "alt": alt,
                "definition": definition,
                "broader": broader,
                "related": related,
                "origin": origin,
                "created": at,
                "updated": at,
                "by": user,
            }
        ),
    )
    _set_related(db, tid, [], related)
    suggest(db, sid)
    return tid


def _definition(text):
    if text is None:
        return None
    text = str(text).strip()
    if len(text) > DEFINITION_MAX:
        raise ValueError(f"A definition can have up to {DEFINITION_MAX} characters.")
    return text or None


def update(db, tid, label=None, alt=None, definition=None, broader=None, related=None, user=None, clear_definition=False):
    """Change what is given; leave the rest."""
    t = _topic(db, tid)
    sid, tid = t["space"], t["id"]
    new_label = _clean_label(label) if label is not None else t["label"]
    new_alt = _clean_alt(alt if alt is not None else t.get("alt"), new_label)
    taken = _labels_taken(db, sid, [key_of(new_label)] + [key_of(a) for a in new_alt], but=tid)
    if taken:
        raise ValueError(f"There is already a topic called {taken}.")
    sets = {"label": new_label, "key": key_of(new_label), "tkey": f"{sid}:{key_of(new_label)}", "alt": new_alt}
    if definition is not None or clear_definition:
        sets["definition"] = None if clear_definition else _definition(definition)
    if broader is not None:
        broader = _links(db, sid, broader, own=tid)
        if _would_loop(db, sid, tid, broader):
            raise ValueError("That would make the topic broader than itself.")
        sets["broader"] = broader
    if related is not None:
        related = _links(db, sid, related, own=tid, what="related")
        _set_related(db, tid, t.get("related") or [], related)
        sets["related"] = related
    sets["updated"] = store.now()
    sets["by"] = user
    db.q("UPDATE $r MERGE $d", r=R("topic", tid), d=sets)
    if "definition" in sets and sets["definition"] is None:
        db.q("UPDATE $r SET definition = NONE", r=R("topic", tid))
    if new_label != t["label"] or new_alt != list(t.get("alt") or []):
        suggest(db, sid)
    return tid


def delete(db, tid):
    """Delete a topic: its narrower topics move up to its broader ones, recordings stop being about it, and an entity it
    was made from shows again. Returns that entity's id, if any."""
    t = _topic(db, tid)
    tid = t["id"]
    for n in db.rows("SELECT record::id(id) AS id, broader FROM topic WHERE space = $s AND $t IN broader", s=t["space"], t=tid):
        up = [b for b in n.get("broader") or [] if b != tid]
        up += [b for b in t.get("broader") or [] if b not in up and b != n["id"]]
        db.q("UPDATE $r SET broader = $b", r=R("topic", n["id"]), b=up)
    _set_related(db, tid, t.get("related") or [], [])
    db.q("DELETE topic_about WHERE topic = $t", t=tid)
    db.q("DELETE $r", r=R("topic", tid))
    eid = (t.get("origin") or {}).get("entity")
    if eid and db.one("SELECT id FROM $r", r=R("entity", int(eid))):
        db.q("UPDATE $r SET hidden = false, hidden_reason = NONE", r=R("entity", int(eid)))
        return int(eid)
    return None


def merge(db, keep, others, user=None):
    """Fold `others` into `keep`: their labels become its other labels, their recordings are about it, and topics that
    named them as broader or related name it instead."""
    k = _topic(db, keep)
    keep = k["id"]
    others = [int(o) for o in dict.fromkeys(others or []) if int(o) != keep]
    if not others:
        raise ValueError("Pick topics to merge into this one.")
    gone = [_topic(db, o) for o in others]
    if any(o["space"] != k["space"] for o in gone):
        raise ValueError("Topics are merged within one namespace.")
    alt = list(k.get("alt") or [])
    broader = list(k.get("broader") or [])
    related = list(k.get("related") or [])
    for o in gone:
        alt += [o["label"], *(o.get("alt") or [])]
        broader += o.get("broader") or []
        related += o.get("related") or []
        for a in db.rows("SELECT recording, source, weight, status FROM topic_about WHERE topic = $t", t=o["id"]):
            mine = db.one("SELECT status, weight FROM $r", r=_about_id(a["recording"], keep))
            if not mine or (mine.get("status") != "accepted" and a.get("status") == "accepted"):
                _about(
                    db, k["space"], a["recording"], keep, a.get("source") or "person", a.get("weight"), a.get("status") or "accepted", user
                )
        db.q("DELETE topic_about WHERE topic = $t", t=o["id"])
    ids = set(others)
    for t in db.rows("SELECT record::id(id) AS id, broader, related FROM topic WHERE space = $s", s=k["space"]):
        if t["id"] in ids or t["id"] == keep:
            continue
        b, r = t.get("broader") or [], t.get("related") or []
        if ids & set(b) or ids & set(r):
            nb = list(dict.fromkeys(keep if x in ids else x for x in b))
            nr = list(dict.fromkeys(keep if x in ids else x for x in r))
            db.q("UPDATE $r SET broader = $b, related = $rel", r=R("topic", t["id"]), b=nb, rel=nr)
    for o in others:
        db.q("DELETE $r", r=R("topic", o))
    broader = [b for b in dict.fromkeys(broader) if b not in ids and b != keep]
    if _would_loop(db, k["space"], keep, broader):
        broader = list(k.get("broader") or [])
    related = [r for r in dict.fromkeys(related) if r not in ids and r != keep]
    db.q(
        "UPDATE $r MERGE $d",
        r=R("topic", keep),
        d={"alt": _clean_alt(alt, k["label"])[:ALT_MAX], "broader": broader, "related": related, "updated": store.now(), "by": user},
    )
    suggest(db, k["space"])
    return keep


def _about(db, sid, rid, tid, source, weight, status, user):
    db.q(
        "UPSERT $r CONTENT $d",
        r=_about_id(rid, tid),
        d=store.clean(
            {
                "space": int(sid),
                "recording": int(rid),
                "topic": int(tid),
                "source": source,
                "weight": weight,
                "status": status,
                "at": store.now(),
                "by": user,
            }
        ),
    )


def tag(db, tid, recordings, remove=False, user=None):
    """Say recordings are about a topic (accepting a suggestion too), or with `remove`, that they aren't."""
    t = _topic(db, tid)
    rids = [int(r) for r in dict.fromkeys(recordings or [])]
    if rids:
        found = {
            r["id"]: r["space"]
            for r in db.rows("SELECT record::id(id) AS id, space FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in rids])
        }
        for r in rids:
            if found.get(r) != t["space"]:
                raise KeyError(r)
    for r in rids:
        if remove:
            prior = db.one("SELECT source FROM $r", r=_about_id(r, t["id"])) or {}
            if prior.get("source") == "analysis":  # remembered, so analysis doesn't suggest it again
                db.q("UPDATE $r SET status = 'dismissed', at = $t, by = $u", r=_about_id(r, t["id"]), t=store.now(), u=user)
            else:
                db.q("DELETE $r", r=_about_id(r, t["id"]))
        else:
            prior = db.one("SELECT source, weight FROM $r", r=_about_id(r, t["id"])) or {}
            _about(db, t["space"], r, t["id"], prior.get("source") or "person", prior.get("weight"), "accepted", user)
    return len(rids)


def from_entity(db, eid, user=None):
    """Make an entity of type TERM a topic: its name the label, its aliases other labels, its description the
    definition, and the recordings that mention it about it. The entity is hidden (deleting the topic shows it again).
    An existing topic of that name gets the recordings instead. Returns the topic's id."""
    e = db.one("SELECT record::id(id) AS id, space, name, type, description, builtin, hidden FROM $r", r=R("entity", int(eid)))
    if not e:
        raise KeyError(eid)
    if e.get("builtin"):
        raise ValueError(f"{e['name']} is always there: it can't become a topic.")
    if e.get("type") != "TERM":
        raise ValueError(f"{e['name']} isn't a topic-like entity (type TERM); retype it first if it is one.")
    sid = e["space"]
    alt = [a["key"] for a in db.rows("SELECT key FROM entity_alias WHERE entity = $e", e=e["id"])]
    row = db.one("SELECT record::id(id) AS id FROM topic WHERE tkey = $k", k=f"{sid}:{key_of(e['name'])}")
    if row:
        tid = row["id"]
    else:
        alt = [a for a in alt if not _labels_taken(db, sid, [key_of(a)])]
        tid = create(db, sid, e["name"], alt, e.get("description"), origin={"entity": e["id"]}, user=user)
    for m in db.rows("SELECT recording, count() AS n FROM mentions WHERE entity = $e GROUP BY recording", e=e["id"]):
        prior = db.one("SELECT status FROM $r", r=_about_id(m["recording"], tid))
        if not prior or prior.get("status") != "accepted":
            _about(db, sid, m["recording"], tid, "entity", m["n"], "accepted", user)
    db.q("UPDATE $r SET hidden = true, hidden_reason = $why", r=R("entity", e["id"]), why=f"became topic {tid}")
    return tid


# ---------- reading ----------
def _counts(db, sids):
    n = Counter()
    for a in db.rows("SELECT topic, count() AS n FROM topic_about WHERE space IN $s AND status = 'accepted' GROUP BY topic", s=list(sids)):
        n[a["topic"]] = a["n"]
    return n


def _out(t, names, recordings=0, narrower=0):
    return {
        "id": t["id"],
        "namespace": names.get(t["space"]),
        "label": t["label"],
        "alt": t.get("alt") or [],
        "definition": t.get("definition"),
        "broader": t.get("broader") or [],
        "related": t.get("related") or [],
        "recordings": recordings,
        "narrower": narrower,
        "from_entity": (t.get("origin") or {}).get("entity"),
        "updated": t.get("updated"),
    }


def list_topics(db, spaces, q="", top=False, broader=None, limit=200, offset=0):
    """Topics of the namespaces in `spaces`, by label: {items, total}. `q` matches labels and other labels, `top` keeps
    the ones with no broader topic, `broader` the narrower topics of one."""
    sids = sorted(int(s) for s in spaces or [])
    if not sids:
        return {"items": [], "total": 0}
    names = store.space_names(db)
    rows = db.rows(f"SELECT {FIELDS} FROM topic WHERE space IN $s", s=sids)
    narrower = Counter(b for t in rows for b in t.get("broader") or [])
    want = key_of(q) if q else ""
    out = []
    for t in rows:
        if want and not any(want in key_of(x) for x in [t["label"], *(t.get("alt") or [])]):
            continue
        if top and t.get("broader"):
            continue
        if broader is not None and int(broader) not in (t.get("broader") or []):
            continue
        out.append(t)
    out.sort(key=lambda t: (t["label"].lower(), t["id"]))
    counts = _counts(db, sids)
    limit = max(1, min(int(limit or 200), 1000))
    page = out[int(offset or 0) : int(offset or 0) + limit]
    return {"items": [_out(t, names, counts[t["id"]], narrower[t["id"]]) for t in page], "total": len(out)}


def detail(db, tid, spaces=None):
    """One topic with its broader, narrower and related topics (labelled) and the recordings about it."""
    t = _topic(db, tid)
    if spaces is not None and t["space"] not in spaces:
        raise KeyError(tid)
    names = store.space_names(db)
    peers = {x["id"]: x for x in db.rows("SELECT record::id(id) AS id, label, broader FROM topic WHERE space = $s", s=t["space"])}
    narrow = sorted((x for x in peers.values() if t["id"] in (x.get("broader") or [])), key=lambda x: x["label"].lower())
    about = db.rows(
        "SELECT recording, source, weight, status, at FROM topic_about WHERE topic = $t AND status != 'dismissed' ORDER BY status, weight DESC",
        t=t["id"],
    )
    titles = {
        r["id"]: r.get("title")
        for r in (
            db.rows(
                "SELECT record::id(id) AS id, title FROM recording WHERE id IN $ids",
                ids=[R("recording", a["recording"]) for a in about],
            )
            if about
            else []
        )
    }
    accepted = [a for a in about if a.get("status") == "accepted"]
    out = _out(t, names, len(accepted), len(narrow))

    def ref(i):
        return {"id": i, "label": peers[i]["label"]} if i in peers else None

    out["broader"] = [x for x in map(ref, t.get("broader") or []) if x]
    out["narrower"] = [{"id": x["id"], "label": x["label"]} for x in narrow]
    out["related"] = [x for x in map(ref, t.get("related") or []) if x]
    out["about"] = [
        {
            "recording": a["recording"],
            "title": titles.get(a["recording"]),
            "source": a.get("source"),
            "weight": a.get("weight"),
            "status": a.get("status"),
        }
        for a in about
        if a["recording"] in titles
    ]
    return out


def of_recording(db, rid, status=None):
    """The topics a recording is about: [{id, label, source, weight, status}]."""
    rows = db.rows("SELECT topic, source, weight, status FROM topic_about WHERE recording = $r AND status != 'dismissed'", r=int(rid))
    if status:
        rows = [a for a in rows if a.get("status") == status]
    labels = {
        t["id"]: t["label"]
        for t in (
            db.rows("SELECT record::id(id) AS id, label FROM topic WHERE id IN $ids", ids=[R("topic", a["topic"]) for a in rows])
            if rows
            else []
        )
    }
    out = [
        {"id": a["topic"], "label": labels[a["topic"]], "source": a.get("source"), "weight": a.get("weight"), "status": a.get("status")}
        for a in rows
        if a["topic"] in labels
    ]
    return sorted(out, key=lambda x: (x["status"] != "accepted", x["label"].lower()))


# ---------- suggestions from analysis ----------
def match_key(label):
    """A label as the keyword index keeps words: stemmed, short and stop words dropped ("Gene therapy" -> "gene therapi")."""
    return " ".join(st for st, _ in analyze.words(str(label or "")))


def _vocab(db, sid):
    """The namespace's labels by key, and by match key for labels of one or two words (the keyword index's)."""
    by_key, by_term = {}, {}
    for t in db.rows("SELECT record::id(id) AS id, label, alt FROM topic WHERE space = $s", s=int(sid)):
        for label in [t["label"], *(t.get("alt") or [])]:
            by_key.setdefault(key_of(label), t["id"])
            m = match_key(label)
            if m and m.count(" ") <= 1:
                by_term.setdefault(m, t["id"])
    return by_key, by_term


def _named(by_key, by_term, phrase):
    return by_key.get(key_of(phrase)) or by_term.get(match_key(phrase))


def suggest(db, sid, rids=None):
    """Suggest the vocabulary's topics for the recordings of namespace `sid` (or just `rids`): a recording is about a
    topic when its summary names one of the topic's labels, or its transcript says one (of a word or two) at least
    SUGGEST_MIN times. The weight is how often, plus SUMMARY_WEIGHT when the summary names it. What people accepted or
    dismissed stays; analysis suggestions that no longer match go. No model is called. Returns the suggestions made."""
    sid = int(sid)
    by_key, by_term = _vocab(db, sid)
    only = [int(r) for r in rids] if rids is not None else None
    if only == []:
        return 0
    found: Counter = Counter()
    if by_term:
        rows = db.rows(
            "SELECT recording, term, n FROM term WHERE space = $s AND term IN $t" + (" AND recording IN $r" if only else ""),
            s=sid,
            t=sorted(by_term),
            r=only,
        )
        for row in rows:
            if row["n"] >= SUGGEST_MIN:
                k = (row["recording"], by_term[row["term"]])
                found[k] = max(found[k], row["n"])
    if by_key:
        rows = db.rows(
            "SELECT record::id(id) AS id, summary.topics AS topics FROM recording WHERE space = $s AND summary"
            + (" AND id IN $r" if only else ""),
            s=sid,
            r=[R("recording", i) for i in only or []],
        )
        for row in rows:
            for tid in {_named(by_key, by_term, p) for p in row.get("topics") or []} - {None}:
                found[(row["id"], tid)] += SUMMARY_WEIGHT
    have = db.rows(
        "SELECT recording, topic, source, status FROM topic_about WHERE space = $s" + (" AND recording IN $r" if only else ""),
        s=sid,
        r=only,
    )
    held = {(a["recording"], a["topic"]): a for a in have}
    for (rid, tid), a in held.items():
        if a.get("source") == "analysis" and a.get("status") == "suggested" and (rid, tid) not in found:
            db.q("DELETE $r", r=_about_id(rid, tid))
    made = 0
    for (rid, tid), weight in found.items():
        a = held.get((rid, tid))
        if a is None or (a.get("source") == "analysis" and a.get("status") == "suggested"):
            _about(db, sid, rid, tid, "analysis", float(weight), "suggested", None)
            made += 1
    return made


def candidates(db, sid, limit=CANDIDATES_MAX):
    """What the namespace's summaries say recordings are about that no topic's label covers and nobody skipped, the
    most recordings first: [{label, recordings}]. People add the ones they want to the vocabulary."""
    sid = int(sid)
    by_key, by_term = _vocab(db, sid)
    space = db.one("SELECT topic_skips FROM $r", r=R("space", sid)) or {}
    skipped = set(space.get("topic_skips") or [])
    seen: dict[str, dict] = {}
    rows = db.rows("SELECT record::id(id) AS id, summary.topics AS topics FROM recording WHERE space = $s AND summary ORDER BY id", s=sid)
    for row in rows:
        for phrase in row.get("topics") or []:
            label = " ".join(str(phrase or "").split())[:LABEL_MAX]
            k = key_of(label)
            if not k or k in skipped or _named(by_key, by_term, label):
                continue
            c = seen.setdefault(k, {"spellings": Counter(), "recordings": set()})
            c["spellings"][label] += 1  # the most common spelling, else the first
            c["recordings"].add(row["id"])
    out = [{"label": c["spellings"].most_common(1)[0][0], "recordings": len(c["recordings"])} for c in seen.values()]
    return sorted(out, key=lambda c: (-c["recordings"], c["label"].lower()))[: max(0, int(limit))]


def skip_candidate(db, sid, label):
    """Don't offer this label as a new topic again."""
    k = key_of(label)
    if not k:
        raise ValueError("Say which label to skip.")
    db.q("UPDATE $r SET topic_skips = array::union(topic_skips ?? [], [$k])", r=R("space", int(sid)), k=k)
