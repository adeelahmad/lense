"""Content types: what kind of resource something is, so the right pipeline runs on it.

Every resource has one of four base types, read from its file: video, audio, image or text (transcripts, documents and
web pages are text). Under each base type is a vocabulary of subtypes, e.g. podcast or interview under audio, or a
screen-share tutorial under video. A subtype has a label, a description, an optional pipeline and optional rules that
recognise it (file extensions, a pattern in the file name, a length). Lens starts with a few; they can all be edited,
the ones that aren't a base type's general subtype can be removed, and people can add their own.

A resource's subtype is the one someone chose (`recording.content_type`), else the first subtype of its base type
whose rules all match, else its base type's general subtype. The pipeline that runs is, in order: the one chosen for
the run, the namespace's override for the subtype, the subtype's own pipeline, the namespace default, the standard
pipeline (pipelines.resolve). Subtypes start with no pipeline, so nothing changes until someone sets one.
"""

from __future__ import annotations

import pathlib
import re

from . import render, store

R = store.R
BASES = ("video", "audio", "image", "text")
KEY_RX = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
RULES = {"extensions", "pattern", "min_minutes", "max_minutes"}
FIELDS = "record::id(id) AS key, base, label, description, pipeline, rules, general, builtin, ord"
MAX_TYPES = 200

# (key, base, label, description, rules); the first of each base is its general subtype.
DEFAULTS = [
    ("video", "video", "Video", "Any video", {}),
    ("screen_tutorial", "video", "Screen-share tutorial", "A recorded screen: demos, walkthroughs, tutorials",
     {"pattern": r"screen|tutorial|demo|walkthrough|how[ _-]?to"}),
    ("meeting_video", "video", "Recorded meeting", "A video call: Zoom, Teams, Meet", {"pattern": r"zoom|teams|meet|meeting"}),
    ("audio", "audio", "Audio", "Any audio", {}),
    ("podcast", "audio", "Podcast", "An episode of a show", {"pattern": r"podcast|episode|\bep\s?\d+"}),
    ("interview", "audio", "Interview", "One person asking, another answering", {"pattern": r"interview"}),
    ("meeting_audio", "audio", "Meeting", "A call or a meeting", {"pattern": r"meeting|call|standup|sync"}),
    ("image", "image", "Image", "Any picture", {}),
    ("scan", "image", "Scanned page", "A page from a scanner or a phone", {"pattern": r"scan"}),
    ("photo", "image", "Photo", "A photograph", {"extensions": [".jpg", ".jpeg", ".heic"]}),
    ("text", "text", "Text", "Any text", {}),
    ("transcript", "text", "Transcript", "Who said what, imported as text", {"extensions": [".srt", ".vtt", ".json", ".jsonl"]}),
    ("document", "text", "Document", "A PDF, Word or Markdown file", {"extensions": [".pdf", ".docx", ".doc", ".md", ".odt", ".rtf"]}),
    ("web_page", "text", "Web page", "A captured web page", {"extensions": [".html", ".htm"]}),
]  # fmt: skip


def base_of(rec):
    """A resource's base type from its file."""
    k = render.kind(rec)
    return k if k in BASES else "text"


def seed(db):
    """The starting vocabulary, on an archive that has none."""
    if db.values("SELECT VALUE id FROM content_type LIMIT 1"):
        return
    seen = set()
    for k, (key, base, label, desc, rules) in enumerate(DEFAULTS):
        db.q(
            "CREATE $r CONTENT $d",
            r=R("content_type", key),
            d=store.clean(
                {
                    "base": base,
                    "label": label,
                    "description": desc,
                    "rules": rules or None,
                    "general": base not in seen,
                    "builtin": True,
                    "ord": k,
                    "created_at": store.now(),
                }
            ),
        )
        seen.add(base)


def all_types(db):
    seed(db)
    rows = db.rows(f"SELECT {FIELDS} FROM content_type")
    for r in rows:
        r["general"], r["builtin"] = bool(r.get("general")), bool(r.get("builtin"))
    return sorted(rows, key=lambda r: (BASES.index(r["base"]), not r["general"], r.get("ord") or 0, r["key"]))


def get(db, key):
    seed(db)
    t = db.one(f"SELECT {FIELDS} FROM $r", r=R("content_type", str(key)))
    if not t:
        raise KeyError(key)
    return t


def _clean_rules(rules):
    rules = dict(rules or {})
    extra = sorted(set(rules) - RULES)
    if extra:
        raise ValueError(f"rules are {', '.join(sorted(RULES))}; not {extra[0]}")
    out = {}
    exts = rules.get("extensions")
    if exts:
        if not isinstance(exts, list) or not all(isinstance(x, str) and x.strip() for x in exts):
            raise ValueError("extensions are a list like [.mp3, .m4a]")
        out["extensions"] = sorted({"." + x.strip().lower().lstrip(".") for x in exts})
    if rules.get("pattern"):
        try:
            re.compile(str(rules["pattern"]), re.I)
        except re.error as e:
            raise ValueError(f"the file name pattern doesn't work ({e})") from None
        out["pattern"] = str(rules["pattern"])[:300]
    for k in ("min_minutes", "max_minutes"):
        v = rules.get(k)
        if v is not None:
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                raise ValueError(f"{k} is a number of minutes")
            out[k] = v
    return out or None


def _check_pipeline(db, pid):
    if pid is None:
        return None
    from . import pipelines

    try:
        pipelines.get(db, int(pid))
    except (KeyError, TypeError, ValueError):
        raise ValueError("no such pipeline") from None
    return int(pid)


def create(db, base, label, key=None, description=None, pipeline=None, rules=None):
    seed(db)
    if base not in BASES:
        raise ValueError(f"the base type is one of {', '.join(BASES)}")
    label = (label or "").strip()[:80]
    if not label:
        raise ValueError("give the content type a name")
    key = key or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if key and not key[0].isalpha():
        key = "t_" + key
    if not KEY_RX.match(key or ""):
        raise ValueError("its key is lowercase letters, digits and _, starting with a letter")
    if db.one("SELECT id FROM $r", r=R("content_type", key)):
        raise ValueError(f"there's a content type called {key} already")
    if len(db.values("SELECT VALUE id FROM content_type")) >= MAX_TYPES:
        raise ValueError(f"there can be up to {MAX_TYPES} content types")
    ords = db.values("SELECT VALUE ord FROM content_type WHERE base = $b", b=base) or [0]
    db.q(
        "CREATE $r CONTENT $d",
        r=R("content_type", key),
        d=store.clean(
            {
                "base": base,
                "label": label,
                "description": (description or "").strip()[:300] or None,
                "pipeline": _check_pipeline(db, pipeline),
                "rules": _clean_rules(rules),
                "general": False,
                "builtin": False,
                "ord": max(o or 0 for o in ords) + 1,
                "created_at": store.now(),
            }
        ),
    )
    return key


def update(db, key, changes):
    """Change a subtype's label, description, pipeline (None for none) or rules; its key and base type stay."""
    get(db, key)
    sets, p = [], {}
    if "label" in changes:
        label = (changes["label"] or "").strip()[:80]
        if not label:
            raise ValueError("give the content type a name")
        sets.append("label = $label")
        p["label"] = label
    if "description" in changes:
        sets.append("description = $desc")
        p["desc"] = (changes["description"] or "").strip()[:300] or None
    if "pipeline" in changes:
        sets.append("pipeline = $pipe")
        p["pipe"] = _check_pipeline(db, changes["pipeline"])
    if "rules" in changes:
        sets.append("rules = $rules")
        p["rules"] = _clean_rules(changes["rules"])
    if sets:
        db.q("UPDATE $r SET " + ", ".join(sets), r=R("content_type", key), **p)


def delete(db, key):
    """Remove a subtype; resources that had it go back to being recognised, namespaces' overrides for it go."""
    t = get(db, key)
    if t.get("general"):
        raise ValueError(f"{t['label']} is the general {t['base']} type; it can be renamed, not removed")
    db.q("UPDATE recording SET content_type = NONE WHERE content_type = $k", k=key)
    for s in db.rows("SELECT record::id(id) AS id, pipelines FROM space WHERE pipelines != NONE"):
        m = dict(s.get("pipelines") or {})
        if m.pop(key, None) is not None:
            db.q("UPDATE $s SET pipelines = $m", s=R("space", s["id"]), m=m or None)
    db.q("DELETE $r", r=R("content_type", key))


def matches(rules, filename, text, minutes):
    """Whether every rule holds: the file's extension, the pattern somewhere in its name or title, its length."""
    if not rules:
        return False
    ext = pathlib.PurePosixPath(filename or "").suffix.lower()
    return not (
        ("extensions" in rules and ext not in rules["extensions"])
        or ("pattern" in rules and not re.search(rules["pattern"], text or "", re.I))
        or ("min_minutes" in rules and (minutes is None or minutes < rules["min_minutes"]))
        or ("max_minutes" in rules and (minutes is None or minutes > rules["max_minutes"]))
    )


def recognise(db, rec, types=None):
    """The subtype a resource's file says it is: the first of its base type whose rules all match, else the general."""
    base = base_of(rec)
    types = [t for t in (types or all_types(db)) if t["base"] == base]
    filename = pathlib.PurePosixPath(rec.get("path") or "").name or (rec.get("title") or "")
    text = " ".join(x for x in (filename, rec.get("title")) if x)
    minutes = rec["duration_ms"] / 60000 if rec.get("duration_ms") else None
    for t in types:
        if not t["general"] and matches(t.get("rules"), filename, text, minutes):
            return t
    return next((t for t in types if t["general"]), types[0] if types else None)


def of_recording(db, rid):
    """(the resource's subtype, whether someone chose it)."""
    rec = db.one("SELECT source, media, path, title, duration_ms, content_type FROM $r", r=R("recording", int(rid)))
    if not rec:
        raise KeyError(rid)
    types = all_types(db)
    if rec.get("content_type"):
        chosen = next((t for t in types if t["key"] == rec["content_type"]), None)
        if chosen and chosen["base"] == base_of(rec):
            return chosen, True
    return recognise(db, rec, types), False


def choose(db, rid, key):
    """Set a resource's subtype (None: recognise it from the file again). It has to be of the resource's base type."""
    rec = db.one("SELECT source, media FROM $r", r=R("recording", int(rid)))
    if not rec:
        raise KeyError(rid)
    if key is not None:
        t = get(db, key) if db.one("SELECT id FROM $r", r=R("content_type", str(key))) else None
        if not t:
            raise ValueError("no such content type")
        if t["base"] != base_of(rec):
            raise ValueError(f"this is {base_of(rec)}; {t['label']} is a {t['base']} type")
    db.q("UPDATE $r SET content_type = $k", r=R("recording", int(rid)), k=key)
