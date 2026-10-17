"""Classify an asset or a piece of text: which of some labels fits, how sure that is, and the other labels ranked,
the cheapest way that works. The decision model answers (decide.py: Jev, typesafe.ai's System One, or Laya on the
machine), else the language model asked for JSON; each call goes in the activity ledger, counted for the asset and
its namespace.

The labels are given, as {label: what it means} or a list of labels, or come from a vocabulary Lens keeps:

- `content_type`: the content types of the asset's base type (content_types.py), or all of them for text;
- `topic`: its namespace's topics (topics.py), with their definitions and other labels; the most used first when there
  are more than MAX_LABELS.

A resource is described to the model by its title, file name, kind, length, language, tags, summary, the entities it
mentions most, the topics it is about and the start of its text, the archive's words marked as data (untrusted.py).
Text given is marked the same way.

`apply` acts on a sure answer (decide.sure: at least decisions.act_above, Settings → AI assistant): the asset's
content type is set (as choosing it on the resource does), the topic is suggested for it (it waits for someone to
accept it; one already accepted, suggested or dismissed stays as it is), or the label is added as a tag. An answer that isn't sure changes nothing; the ranked labels go back for
the person to pick.
"""

from __future__ import annotations

import pathlib

from . import activity, content_types as ctypes, decide, library, render, store, topics as topicmod, untrusted

R = store.R
VOCABULARIES = ("content_type", "topic")
MAX_LABELS = 60  # labels one question offers: decision models read about 32k tokens of state and criteria
LABEL_CHARS, MEANING_CHARS = 80, 300
TEXT_CHARS = 6000  # of a resource's text, or of text given, that the model reads
QUESTION_CHARS = 500


def _labels(labels):
    """Given labels as {label: what it means}, checked. A list's items are labels, or "label: what it means"."""
    if isinstance(labels, (list, tuple)):
        labels = dict(tuple(x.strip() for x in str(item).partition(": ")[::2]) for item in labels)
    if not isinstance(labels, dict):
        raise ValueError("labels are a list of names, or {name: what it means}")
    out = {}
    for k, v in labels.items():
        name = " ".join(str(k).split())
        if not name:
            continue
        if len(name) > LABEL_CHARS:
            raise ValueError(f"a label has at most {LABEL_CHARS} characters")
        if v is not None and not isinstance(v, str):
            raise ValueError(f"what {name} means is text")
        out[name] = " ".join((v or "").split())[:MEANING_CHARS] or name
    if len(out) < 2:
        raise ValueError("give at least two labels to choose from")
    if len(out) > MAX_LABELS:
        raise ValueError(f"at most {MAX_LABELS} labels")
    return out


def vocabulary(db, kind, rec=None, space=None):
    """({label: what it means}, {label: what it stands for}) of a vocabulary Lens keeps, for a resource (`rec`) or a
    namespace (`space`)."""
    if kind == "content_type":
        base = ctypes.base_of(rec) if rec else None
        types = [t for t in ctypes.all_types(db) if base is None or t["base"] == base]
        return (
            {t["label"]: f"{t['description'] or t['label']} ({t['base']})" for t in types},
            {t["label"]: {"key": t["key"]} for t in types},
        )
    if kind == "topic":
        sid = rec["space"] if rec else space
        if sid is None:
            raise ValueError("topics are a namespace's: classify a resource, or name the namespace")
        rows = db.rows(f"SELECT {topicmod.FIELDS} FROM topic WHERE space = $s", s=sid)
        if len(rows) < 2:
            raise ValueError("this namespace has fewer than two topics to choose from")
        used = topicmod._counts(db, [sid])
        rows = sorted(rows, key=lambda t: (-used[t["id"]], t["label"].casefold()))[:MAX_LABELS]
        meaning = {
            t["label"]: "; ".join(x for x in (t.get("definition"), ", ".join(t.get("alt") or [])) if x)[:MEANING_CHARS] or t["label"]
            for t in rows
        }
        return meaning, {t["label"]: {"id": t["id"]} for t in rows}
    raise ValueError(f"vocabulary is one of {', '.join(VOCABULARIES)}")


def describe(db, cfg, rid):
    """What the model reads about a resource."""
    rec = db.one(
        "SELECT title, path, source, media, duration_ms, language, tags, summary, space FROM $r",
        r=R("recording", int(rid)),
    )
    if not rec:
        raise KeyError(rid)
    lines = db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx LIMIT 400", r=int(rid))
    text = ""
    for line in lines:
        if len(text) >= TEXT_CHARS:
            break
        text += (line.get("text") or "") + "\n"
    ents = db.rows("SELECT entity, count() AS n FROM mentions WHERE recording = $r GROUP BY entity ORDER BY n DESC LIMIT 15", r=int(rid))
    names = {
        e["id"]: e["name"]
        for e in (
            db.rows("SELECT record::id(id) AS id, name FROM entity WHERE id IN $ids", ids=[R("entity", e["entity"]) for e in ents])
            if ents
            else []
        )
    }
    summary = rec.get("summary") if isinstance(rec.get("summary"), dict) else {}
    about = [t["label"] for t in topicmod.of_recording(db, rid, status="accepted")]
    words = {
        "title": rec.get("title"),
        "summary": summary.get("summary"),
        "entities": [names[e["entity"]] for e in ents if e["entity"] in names],
        "text": text[:TEXT_CHARS],
    }
    return store.clean(
        {
            "kind": render.kind(rec),
            "file": pathlib.PurePosixPath(rec.get("path") or "").name or None,
            "length": store.tc(rec["duration_ms"]) if rec.get("duration_ms") else None,
            "language": rec.get("language"),
            "tags": rec.get("tags") or None,
            "topics": about or None,
            "resource": untrusted.wrap(_lines(words), "the resource", cfg),
        }
    )


def _lines(words):
    out = []
    for k, v in words.items():
        if v:
            out.append(f"{k}: {', '.join(v) if isinstance(v, list) else v}")
    return "\n".join(out)


def classify(db, cfg, rid=None, text=None, labels=None, vocab=None, question=None, apply=False, user=None, space=None):
    """Which label fits the resource `rid` (or `text`): {choice, confidence, sure, ranked, by, vocabulary, applied,
    key?, id?}. Raises ValueError for a bad question and decide.Undecided when no model could answer."""
    if (rid is None) == (text is None or not str(text).strip()):
        raise ValueError("classify an asset or a text: give one")
    if (labels is None) == (vocab is None):
        raise ValueError("give labels, or a vocabulary (content_type or topic): one")
    if apply and rid is None:
        raise ValueError("only a resource's classification can be applied")
    rec = db.one(f"SELECT {ctypes.TYPE_FIELDS}, space FROM $r", r=R("recording", int(rid))) if rid is not None else None
    if rid is not None and not rec:
        raise KeyError(rid)
    if vocab:
        options, stands = vocabulary(db, vocab, rec, space)
    else:
        options, stands = _labels(labels), {}
        if apply and any(len(k) > library.TAG_MAX for k in options):
            raise ValueError(f"a label applied as a tag has at most {library.TAG_MAX} characters")
    what = "resource" if rid is not None else "text"
    asked = " ".join((question or "").split())[:QUESTION_CHARS] or {
        "content_type": f"What kind of {ctypes.base_of(rec) if rec else 'content'} is this {what}?",
        "topic": f"Which topic is this {what} about?",
    }.get(vocab or "", f"Which label fits this {what} best?")
    state = describe(db, cfg, rid) if rid is not None else {"text": untrusted.wrap(str(text)[:TEXT_CHARS], "the text", cfg)}
    refs = (f"recording:{int(rid)}", f"space:{rec['space']}") if rec else ((f"space:{space}",) if space else ())
    with activity.scope(db, *refs, cfg=cfg):
        d = decide.choose(cfg, asked, options, state)
    sure = decide.sure(cfg, d)
    out = {
        "choice": d["choice"],
        "confidence": round(float(d["confidence"]), 3),
        "sure": sure,
        "ranked": [{"label": x["option"], "p": x["p"], **stands.get(x["option"], {})} for x in d["ranked"]],
        "by": d["by"],
        "vocabulary": vocab,
        "question": asked,
        "applied": None,
        **stands.get(d["choice"], {}),
    }
    if apply and sure:
        out["applied"] = _apply(db, int(rid), vocab, d["choice"], stands, user)
    return out


def _apply(db, rid, vocab, choice, stands, user):
    """Act on a sure answer; what was done (None: nothing needed doing)."""
    if vocab == "content_type":
        ctypes.choose(db, rid, stands[choice]["key"])
        return "content_type"
    if vocab == "topic":  # what already holds, waits or was dismissed stays as it is
        return "topic_suggested" if topicmod.propose(db, stands[choice]["id"], [rid], source="classify", user=user) else None
    library.retag(db, [rid], add=[choice])
    return "tag"
