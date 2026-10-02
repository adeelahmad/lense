"""Suggestions for a new resource from a decision model (app/domain/decide.py, docs/processing.md#classify): which of
the namespace's tags fit it, which content type it is, and which collection it belongs in.

One request asks all of it. An answer the model is sure of (`decisions.apply_above`) is applied; a likely one waits on
the resource as a suggestion for an editor to accept or dismiss. Tags only come from the ones the namespace already
uses, so the model can't invent a vocabulary. Moving into a collection changes who can read the resource (people with a
role on that collection), so that is only ever suggested unless an admin switched `decisions.route` on.
"""

from __future__ import annotations

from . import auth, content_types, decide, hierarchy, library, metadata, store

R = store.R
TEXT_CHARS = 6000  # the start of the resource's text the model reads
TAG_CANDIDATES = 40  # the namespace's most used tags
COLLECTION_CANDIDATES = 20
SUGGEST_ABOVE = 0.5  # less likely than this isn't worth a person's attention
MODEL = {"id": None, "email": "decision model"}  # who the audit log names for what was applied without a person


def _text(db, rid):
    out, n = [], 0
    for t in (x.get("text") for x in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx LIMIT 400", r=rid)):
        if t and t.strip():
            out.append(t.strip())
            n += len(out[-1]) + 1
            if n >= TEXT_CHARS:
                break
    return "\n".join(out)[:TEXT_CHARS]


def _questions(db, rec, rid):
    """(questions, what each id stands for)."""
    qs, about = {}, {}
    have = {t.casefold() for t in rec.get("tags") or []}
    for n, t in enumerate(x["tag"] for x in library.tag_counts(db, [rec["space"]])[:TAG_CANDIDATES] if x["tag"].casefold() not in have):
        qs[f"t{n}"] = decide.noul(
            f"Is this resource about “{t}”, so that someone browsing the tag “{t}” would expect to find it?",
            "the resource is clearly about this",
            "it isn't about this, or only mentions it in passing",
        )
        about[f"t{n}"] = ("tag", t, t)
    current, chosen = content_types.of_recording(db, rid)
    types = [t for t in content_types.all_types(db) if current and t["base"] == current["base"]]
    if not chosen and len(types) > 1:
        qs["type"] = decide.choice(
            "Which kind of resource is this?", {t["key"]: " — ".join(x for x in (t["label"], t.get("description")) if x) for t in types}
        )
        about["type"] = {t["key"]: t["label"] for t in types} | {"": current["key"]}
    home = store.default_collection(db, rec["space"])
    others = sorted((c for c in hierarchy.of_space(db, rec["space"]) if c["id"] != home), key=lambda c: c["id"])[:COLLECTION_CANDIDATES]
    if others and rec.get("collection") in (None, home):  # only what nobody placed
        options = {f"c{c['id']}": " — ".join(x for x in (_path(db, c), c.get("description")) if x) for c in others}
        options["none"] = "none of the others fits; it stays where unsorted resources are"
        qs["collection"] = decide.choice("Which collection does this resource belong in?", options)
        about["collection"] = {f"c{c['id']}": _path(db, c) for c in others}
    return qs, about


def _path(db, c):
    return " / ".join(s["name"] for s in hierarchy.path(db, c["id"]))


def _key(s):
    return f"{s['kind']}:{str(s['value']).casefold()}"


def classify_recording(db, cfg, rid, say=print):
    """Ask, apply what's sure, keep what's likely. Returns (applied, suggested). decide.DecideError when the model
    can't be asked."""
    rec = db.one(
        "SELECT space, collection, title, tags, summary, suggestions_dismissed, suggestions_applied FROM $r", r=R("recording", rid)
    )
    if not rec:
        raise KeyError(rid)
    text = _text(db, rid)
    qs, about = _questions(db, rec, rid)
    if not text or not qs:
        db.q("UPDATE $r SET suggestions = []", r=R("recording", rid))
        return [], []
    state = {"title": rec.get("title"), "summary": (rec.get("summary") or {}).get("summary"), "text": text}
    got = decide.ask(cfg, {k: v for k, v in state.items() if v}, qs)
    sure, dismissed = decide.threshold(cfg, "apply_above"), set(rec.get("suggestions_dismissed") or [])
    before = set(rec.get("suggestions_applied") or [])  # applied by an earlier run; one that's gone again, a person undid
    found = []
    for qid, a in got.items():
        if qid == "type":
            if a["choice"] != about["type"][""] and a["confidence"] >= SUGGEST_ABOVE:
                found.append({"kind": "content_type", "value": a["choice"], "label": about["type"][a["choice"]], "p": a["confidence"]})
        elif qid == "collection":
            if a["choice"] != "none" and a["confidence"] >= SUGGEST_ABOVE:
                found.append(
                    {"kind": "collection", "value": int(a["choice"][1:]), "label": about["collection"][a["choice"]], "p": a["confidence"]}
                )
        elif a["p"] >= SUGGEST_ABOVE:
            found.append({"kind": "tag", "value": about[qid][1], "label": about[qid][2], "p": a["p"]})
    applied, waiting = [], []
    for s in sorted(found, key=lambda s: -s["p"]):
        s["p"] = round(s["p"], 3)
        by_itself = s["p"] >= sure and (s["kind"] != "collection" or decide.uses(cfg, "route"))
        if _key(s) in before:
            continue
        if by_itself and _apply(db, rid, s):
            applied.append(s)
            # nobody did this: the audit log says the decision model did, and how sure it was
            auth.audit(
                db, MODEL, f"recording.classify.{s['kind']}", f"recording:{rid}", {"value": s["value"], "label": s["label"], "p": s["p"]}
            )
            if s["kind"] == "collection":
                metadata.touched(db, cfg, rid)
        elif _key(s) not in dismissed:
            waiting.append(s | {"id": _key(s)})
    db.q(
        "UPDATE $r SET suggestions = $s, suggestions_applied = $a, classified_at = $t",
        r=R("recording", rid),
        s=waiting,
        a=sorted(before | {_key(s) for s in applied}),
        t=store.now(),
    )
    for s in applied:
        say(f"{s['kind'].replace('_', ' ')} {s['label']}: applied ({round(s['p'] * 100)}% sure)")
    return applied, waiting


def _apply(db, rid, s):
    """Do what a suggestion says; False when it no longer can be (the tags are full, the collection is gone)."""
    try:
        if s["kind"] == "tag":
            tags = (db.one("SELECT tags FROM $r", r=R("recording", rid)) or {}).get("tags") or []
            if len(tags) >= library.TAGS_MAX:
                return False
            library.set_tags(db, rid, [*tags, s["value"]])
        elif s["kind"] == "content_type":
            content_types.choose(db, rid, s["value"])
        else:
            hierarchy.place(db, [rid], s["value"])
    except (KeyError, ValueError):
        return False
    return True


def suggestions(db, rid):
    return (db.one("SELECT suggestions FROM $r", r=R("recording", rid)) or {}).get("suggestions") or []


def settle(db, rid, sid, accept):
    """Accept a waiting suggestion (it's applied) or dismiss it (it isn't suggested again). Returns it. KeyError when
    there's no such suggestion, ValueError when it can't be applied any more."""
    waiting = suggestions(db, rid)
    s = next((x for x in waiting if x["id"] == sid), None)
    if s is None:
        raise KeyError(sid)
    if accept and not _apply(db, rid, s):
        raise ValueError("This can't be applied any more: the resource's tags are full, or what it names is gone.")
    left = [x for x in waiting if x["id"] != sid]
    if accept:
        db.q("UPDATE $r SET suggestions = $s", r=R("recording", rid), s=left)
    else:
        db.q(
            "UPDATE $r SET suggestions = $s, suggestions_dismissed = array::union(suggestions_dismissed ?? [], [$k])",
            r=R("recording", rid),
            s=left,
            k=sid,
        )
    return s
