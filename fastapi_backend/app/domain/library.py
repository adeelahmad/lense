"""The library: recordings listed with filters, sorting and a total count, for everyone who can read them."""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
from collections import Counter, defaultdict

from . import access as acc, metadata, render, store

R = store.R
STATUSES = ("new", "transcribed", "diarized", "analyzed", "error")
STATES = ("processing", "failed")  # job states the status filter also takes: a job queued or running; the latest job failed
MEDIA = ("audio", "video", "transcript")
STATUS_ORDER = ["error", "new", "transcribed", "diarized", "analyzed"]
# sort key -> (the value to sort by, whether a row has none); rows without a value go last either way, then ids break ties
SORTS = {
    "date": ("recorded_at", "recorded_at = NONE"),
    "title": ("string::lowercase(title ?? '')", "(title ?? '') = ''"),
    "duration": ("duration_ms", "duration_ms = NONE"),
    "speakers": ("array::len(array::distinct((SELECT VALUE speaker FROM appearance WHERE recording = record::id($parent.id))))", "false"),
    "status": ("array::find_index($order, status ?? 'new') ?? 9", "false"),
    "importance": ("summary.importance", "summary.importance = NONE"),
}
FIELDS = (
    "record::id(id) AS id, title, recorded_at, duration_ms, status, error, source, stats, summary, space, collection, media, "
    "access, access_parts, featured, tags, language, path, remote, engine"
)
# where a recording came from (origin_of): a connected source (source:<id>), or one of these
ORIGINS = {
    "upload": "Uploaded",
    "paste": "Pasted text",
    "iiif": "IIIF imports",
    "folder": "Archive folders",
    "file": "Imported files",
}
MAX_WORDS = 10
TAG_MAX, TAGS_MAX = 40, 20  # characters in a tag, tags on a recording
MONTHS_MAX = 240  # months in a namespace's stats: the latest twenty years
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def recs(ids):
    return [R("recording", int(i)) for i in ids]


def active_recordings(db, spaces):
    """Recordings with a job queued or running."""
    return set(db.values("SELECT VALUE recording FROM job WHERE status IN ['queued', 'running'] AND space IN $s", s=sorted(spaces)))


def failed_recordings(db, spaces):
    """Recordings whose latest job failed (a later job, running or done, takes over from a failed one)."""
    cand = set(db.values("SELECT VALUE recording FROM job WHERE status = 'failed' AND space IN $s", s=sorted(spaces)))
    latest = {}
    for j in db.rows("SELECT recording, record::id(id) AS id, status FROM job WHERE recording IN $r", r=sorted(cand)) if cand else []:
        if j["id"] > latest.get(j["recording"], (0, None))[0]:
            latest[j["recording"]] = (j["id"], j["status"])
    return {r for r, (_, st) in latest.items() if st == "failed"}


def review_recordings(db, spaces):
    """Recordings where a voice match waits for a person: a speaker in them has a suggested merge."""
    sug = db.rows("SELECT speaker, candidate FROM suggestion WHERE space IN $s", s=sorted(spaces))
    cands = sorted({g["candidate"] for g in sug})
    exist = (
        set(db.values("SELECT VALUE record::id(id) FROM speaker WHERE id IN $ids", ids=[R("speaker", c) for c in cands]))
        if cands
        else set()
    )
    spk = sorted({g["speaker"] for g in sug if g["candidate"] in exist})
    return set(db.values("SELECT VALUE recording FROM appearance WHERE speaker IN $s", s=spk)) if spk else set()


def _day(v):
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


# ---------- where recordings came from, their languages, and who edited them ----------
class Origins:
    """Tells where each recording came from: a connected source (``source:<id>``, Sources), an upload, pasted text, a
    IIIF import, one of the archive's own folders (``lens scan``), or another file imported by path."""

    def __init__(self, db, cfg):
        self.uploads = str(pathlib.Path(cfg["data_dir"]) / "uploads") + os.sep
        names = store.space_names(db)
        roots = {
            name: [str(pathlib.Path(p)) + os.sep for p in (spec or {}).get("paths") or []]
            for name, spec in (cfg.get("namespaces") or {}).items()
        }
        self.roots = {sid: roots.get(name, []) for sid, name in names.items()}
        self.db = db

    def of(self, r):
        rm = r.get("remote") or {}
        if rm.get("source") is not None:
            return f"source:{rm['source']}"
        path = str(r.get("path") or "")
        if r.get("engine") == "import:iiif" or path.startswith("iiif:"):
            return "iiif"
        if path.startswith("paste:"):
            return "paste"
        if path.startswith(self.uploads):
            return "upload"
        if any(path.startswith(root) for root in self.roots.get(r.get("space"), [])):
            return "folder"
        return "file"

    def recordings(self, spaces):
        """{recording id: origin} for the recordings of these namespaces."""
        rows = self.db.rows("SELECT record::id(id) AS id, space, path, remote, engine FROM recording WHERE space IN $s", s=sorted(spaces))
        return {r["id"]: self.of(r) for r in rows}

    def names(self, keys):
        """{origin: what to call it}: a source's name, else ORIGINS."""
        ids = sorted({int(k.split(":", 1)[1]) for k in keys if k.startswith("source:")})
        srcs = (
            {
                r["id"]: r["name"]
                for r in self.db.rows(
                    "SELECT record::id(id) AS id, name FROM storage_source WHERE id IN $ids", ids=[R("storage_source", i) for i in ids]
                )
            }
            if ids
            else {}
        )
        return {k: ORIGINS.get(k) or srcs.get(int(k.split(":", 1)[1])) or "A removed source" for k in keys}

    def counts(self, spaces):
        """[{origin, name, recordings}], the most recordings first."""
        n = Counter(self.recordings(spaces).values())
        names = self.names(n)
        return sorted(
            ({"origin": k, "name": names[k], "recordings": c} for k, c in n.items()), key=lambda x: (-x["recordings"], x["name"].casefold())
        )


def check_origins(origins):
    for o in origins or []:
        kind, _, sid = o.partition(":")
        if not (o in ORIGINS or (kind == "source" and sid.isdigit())):
            raise ValueError(f"origin is one of {', '.join(ORIGINS)} or source:<id>")


def language_counts(db, spaces):
    """[{language, recordings}] in these namespaces, the most recordings first; null is "not known"."""
    n = Counter(
        (str(v or "").strip().lower() or None)
        for v in db.values("SELECT VALUE language FROM recording WHERE space IN $s", s=sorted(spaces))
    )
    return sorted(({"language": k, "recordings": c} for k, c in n.items()), key=lambda x: (-x["recordings"], x["language"] or "~"))


def edited_by(db, email):
    """Recordings this person edited: corrected a line of the transcript, changed the catalogue record, or renamed."""
    ids = set(db.values("SELECT VALUE recording FROM segment_edit WHERE by = $e", e=email))
    targets = db.values("SELECT VALUE target FROM meta_edit WHERE by = $e AND string::starts_with(target, 'recording:')", e=email)
    targets += db.values("SELECT VALUE target FROM audit_log WHERE action = 'recording.rename' AND email = $e", e=email)
    ids |= {int(t.split(":", 1)[1]) for t in targets if str(t).split(":", 1)[-1].isdigit()}
    return ids


def where(
    db,
    spaces,
    q=None,
    status=None,
    attention=False,
    processing=False,
    speakers=None,
    date_from=None,
    date_to=None,
    min_duration=None,
    max_duration=None,
    media=None,
    access=None,
    featured=None,
    tags=None,
    origins=None,
    languages=None,
    edited=None,
    collections=None,
    cfg=None,
):
    """The WHERE clause and its parameters for these filters. Filters combine with AND, the values of one filter with OR.

    q: every word in the title, the namespace's name or a speaker's name. status: recording statuses and job states
    (STATES). attention: errored, latest job failed, or a voice match to review. processing: a job queued or running.
    speakers: speaker ids. date_from/date_to: the recording date, inclusive. min/max_duration: seconds, max exclusive.
    access: levels (public, restricted, private), a namespace's default counting for recordings without their own.
    featured: true or false. tags: any of these tags (ignoring case). origins: where they came from (Origins; needs cfg).
    languages: language codes (ignoring case), "none" for recordings whose language isn't known. edited: recording ids
    (edited_by). collections: collection ids (a collection and the ones inside it).
    """
    spaces = sorted(spaces)
    w, p = ["space IN $spaces"], {"spaces": spaces}
    words = (q or "").lower().split()[:MAX_WORDS]
    if words:
        names, people = store.space_names(db), _speakers(db, spaces)
        for i, word in enumerate(words):
            sids = [s["id"] for s in people if word in s["display"]]
            p[f"w{i}"] = word
            p[f"ws{i}"] = [s for s in spaces if word in (names.get(s) or "")]
            p[f"wr{i}"] = recs(db.values("SELECT VALUE recording FROM appearance WHERE speaker IN $s", s=sids)) if sids else []
            w.append(f"((title != NONE AND string::contains(string::lowercase(title), $w{i})) OR space IN $ws{i} OR id IN $wr{i})")
    status = list(dict.fromkeys(status or []))
    if any(s not in STATUSES + STATES for s in status):
        raise ValueError(f"status is one of {', '.join(STATUSES + STATES)}")
    if status:
        ors = []
        plain = [s for s in status if s in STATUSES]
        if plain:
            p["st"] = plain
            ors.append("status IN $st" + (" OR status = NONE" if "new" in plain else ""))
        if "processing" in status:
            p["st_active"] = recs(active_recordings(db, spaces))
            ors.append("id IN $st_active")
        if "failed" in status:
            p["st_failed"] = recs(failed_recordings(db, spaces))
            ors.append("id IN $st_failed")
        w.append("(" + " OR ".join(ors) + ")")
    if attention:
        p["attention"] = recs(failed_recordings(db, spaces) | review_recordings(db, spaces))
        w.append("(status = 'error' OR id IN $attention)")
    if processing:
        p["active"] = recs(active_recordings(db, spaces))
        w.append("id IN $active")
    if speakers:
        ids = sorted({int(x) for x in speakers})
        p["by_speaker"] = recs(db.values("SELECT VALUE recording FROM appearance WHERE speaker IN $s", s=ids))
        w.append("id IN $by_speaker")
    if date_from:
        p["from"] = _day(date_from).isoformat()
        w.append("recorded_at != NONE AND recorded_at >= $from")
    if date_to:  # inclusive: anything before the next day
        p["to"] = (_day(date_to) + dt.timedelta(days=1)).isoformat()
        w.append("recorded_at != NONE AND recorded_at < $to")
    if min_duration is not None:
        p["dmin"] = int(min_duration * 1000)
        w.append("duration_ms != NONE AND duration_ms >= $dmin")
    if max_duration is not None:
        p["dmax"] = int(max_duration * 1000)
        w.append("duration_ms != NONE AND duration_ms < $dmax")
    if media:
        if media not in MEDIA:
            raise ValueError(f"media is one of {', '.join(MEDIA)}")
        w.append(
            {
                "video": "media.kind = 'video'",
                "audio": "(media.kind = 'audio' OR (media.kind = NONE AND source = 'audio'))",
                "transcript": "(media.kind = 'transcript' OR (media.kind = NONE AND source != 'audio'))",
            }[media]
        )
    levels = list(dict.fromkeys(access or []))
    if levels:
        if any(x not in acc.LEVELS for x in levels):
            raise ValueError(f"access is one of {', '.join(acc.LEVELS)}")
        # a recording without its own access follows its namespace's default
        p["lv"] = levels
        p["lv_spaces"] = [sid for sid, (level, _) in acc.namespace_defaults(db, spaces).items() if level in levels]
        w.append("(access IN $lv OR (access = NONE AND space IN $lv_spaces))")
    if featured is not None:
        w.append("featured = true" if featured else "featured != true")
    keys = sorted({" ".join(t.split()).casefold() for t in tags or [] if t and t.strip()})
    if keys:
        p["tag_keys"] = keys
        w.append("tag_keys CONTAINSANY $tag_keys")
    if origins:
        check_origins(origins)
        if cfg is None:
            raise ValueError("origin needs the configuration")
        wanted = set(origins)
        p["by_origin"] = recs(sorted(i for i, o in Origins(db, cfg).recordings(spaces).items() if o in wanted))
        w.append("id IN $by_origin")
    langs = sorted({x.strip().lower() for x in languages or [] if x and x.strip()})
    if langs:
        p["langs"] = [x for x in langs if x != "none"]
        w.append("(string::lowercase(language ?? '') IN $langs" + (" OR language = NONE OR language = ''" if "none" in langs else "") + ")")
    if edited is not None:
        p["edited"] = recs(sorted(edited))
        w.append("id IN $edited")
    if collections is not None:
        p["cols"] = sorted(collections)
        w.append("collection IN $cols")
    return " AND ".join(w), p


def _speakers(db, spaces):
    rows = db.rows("SELECT record::id(id) AS id, name, label FROM speaker WHERE space IN $s", s=spaces)
    return [{"id": r["id"], "display": (r.get("name") or r.get("label") or "").lower()} for r in rows]


def list_recordings(db, spaces, sort="-date", limit=500, offset=0, **filters):
    """(one page of recording summaries, how many match in all). `sort` is a key of SORTS, prefixed with - for descending.
    With ``cfg`` among the filters, rows say where they came from (``origin``, ``origin_name``)."""
    sort = sort or "-date"
    key, desc = sort.lstrip("-"), sort.startswith("-")
    if key not in SORTS:
        raise ValueError(f"sort by {', '.join(SORTS)} (prefix - for descending)")
    if not spaces:
        return [], 0
    cond, p = where(db, spaces, **filters)
    expr, missing = SORTS[key]
    d = "DESC" if desc else "ASC"
    rows = db.rows(
        f"SELECT {FIELDS}, {expr} AS _k, {missing} AS _none FROM recording WHERE {cond} "
        f"ORDER BY _none ASC, _k {d}, id {d} LIMIT {int(limit)} START {int(offset)}",
        order=STATUS_ORDER,
        **p,
    )
    # count() miscounts some OR filters on the embedded engine (2.x: "status IN $st OR status = NONE" counted rows twice);
    # counting the ids the same WHERE selects is exact on both engines
    total = db.values(f"RETURN array::len((SELECT VALUE id FROM recording WHERE {cond}))", **p)
    return summaries(db, rows, filters.get("cfg")), int(total[0]) if total else 0


def summaries(db, rows, cfg=None):
    """List rows as the library shows them: namespace, speakers, media kind, poster frame, emotion mix and summary; with
    cfg, where each came from."""
    ids = [r["id"] for r in rows]
    apps = defaultdict(list)
    for a in db.rows("SELECT recording, speaker FROM appearance WHERE recording IN $r", r=ids) if rows else []:
        apps[a["recording"]].append(a["speaker"])
    names, spaces = render.speaker_names(db, [x for v in apps.values() for x in v]), store.space_names(db)
    cols = (
        {
            c["id"]: c["name"]
            for c in db.rows("SELECT record::id(id) AS id, name FROM collection WHERE space IN $s", s=sorted({r["space"] for r in rows}))
        }
        if rows
        else {}
    )
    access = acc.many(db, rows)
    posters = (
        {x["recording"]: x.get("frame") for x in db.rows("SELECT recording, frame FROM shot WHERE recording IN $r AND idx = 0", r=ids)}
        if rows
        else {}
    )
    origins = Origins(db, cfg) if cfg is not None and rows else None
    came = {r["id"]: origins.of(r) for r in rows} if origins else {}
    called = origins.names(set(came.values())) if origins else {}
    out = []
    for r in rows:
        if origins:
            r["origin"] = came[r["id"]]
            r["origin_name"] = called[r["origin"]]
        for k in ("_k", "_none", "access_parts", "path", "remote", "engine"):
            r.pop(k, None)
        a = access[r["id"]]
        r.update(access=a["access"], open=a["open"], featured=a["featured"], tags=r.get("tags") or [])
        st, sm = r.pop("stats", None) or {}, r.pop("summary", None) or {}
        r["media_kind"] = (r.pop("media", None) or {}).get("kind") or ("audio" if r.get("source") == "audio" else "transcript")
        r["poster"] = f"{store.API}/recordings/{r['id']}/frames/{posters[r['id']]}" if posters.get(r["id"]) else None
        out.append(
            {
                **r,
                "namespace": spaces.get(r["space"]),
                "collection_name": cols.get(r.get("collection")),
                "emotions": st.get("emotions", {}),
                "words": st.get("words"),
                "importance": sm.get("importance"),
                "sentiment": sm.get("sentiment"),
                "speakers": ",".join(dict.fromkeys(names.get(x, "?") for x in apps.get(r["id"], []))),
            }
        )
    return out


# ---------- tags ----------
def clean_tags(values):
    """Tags as people typed them: whitespace collapsed, without repeats (ignoring case), sorted; at most TAG_MAX
    characters each and TAGS_MAX on a recording."""
    if not isinstance(values, list):
        raise ValueError("tags is a list of words")
    out = {}
    for v in values:
        if not isinstance(v, str):
            raise ValueError("a tag is text")
        t = " ".join(v.split())
        if len(t) > TAG_MAX:
            raise ValueError(f"a tag has at most {TAG_MAX} characters")
        if t:
            out.setdefault(t.casefold(), t)
    if len(out) > TAGS_MAX:
        raise ValueError(f"a recording has at most {TAGS_MAX} tags")
    return sorted(out.values(), key=str.casefold)


def _save_tags(db, rid, tags):
    # tag_keys (lowercase) is what the list filters on; tags keep how they were written
    db.q("UPDATE $r SET tags = $t, tag_keys = $k", r=R("recording", rid), t=tags, k=[t.casefold() for t in tags])


def set_tags(db, rid, tags):
    """Replace a recording's tags. Returns (before, after)."""
    rec = db.one("SELECT tags FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    before, after = rec.get("tags") or [], clean_tags(tags)
    if after != before:
        _save_tags(db, rid, after)
    return before, after


def retag(db, rids, add=(), remove=()):
    """Add and remove tags on several recordings (a tag both added and removed is added). Returns how many changed."""
    add, gone = clean_tags(list(add)), {t.casefold() for t in clean_tags(list(remove))} - {t.casefold() for t in add}
    changed = 0
    for r in db.rows("SELECT record::id(id) AS id, tags FROM recording WHERE id IN $ids", ids=recs(rids)):
        before = r.get("tags") or []
        after = clean_tags([t for t in before if t.casefold() not in gone] + add)
        if after != before:
            _save_tags(db, r["id"], after)
            changed += 1
    return changed


def tag_counts(db, spaces):
    """The tags on these namespaces' recordings with how many recordings have each, most used first; each spelled as
    most of its recordings spell it."""
    counts, spellings = Counter(), defaultdict(Counter)
    for tags in db.values("SELECT VALUE tags FROM recording WHERE space IN $s AND tags != NONE", s=sorted(spaces)) if spaces else []:
        for t in tags or []:
            counts[t.casefold()] += 1
            spellings[t.casefold()][t] += 1
    return [{"tag": spellings[k].most_common(1)[0][0], "recordings": n} for k, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def rename(db, cfg, rid, title):
    """A new title (whitespace collapsed, at most 200 characters). Returns (old, new).

    The recording's report page is named after its title, so the page moves with it; the report job that follows
    rewrites the title inside."""
    title = " ".join((title or "").split())[:200]
    if not title:
        raise ValueError("the title can't be empty")
    rec = db.one("SELECT title, space FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    if rec.get("title") == title:
        return title, title
    db.q("UPDATE $r SET title = $t", r=R("recording", rid), t=title)
    folder = pathlib.Path(cfg["data_dir"]) / "reports" / (store.space_names(db).get(rec["space"]) or "_")
    old, new = folder / f"{render.slug(rec.get('title'))}-{rid}.html", folder / f"{render.slug(title)}-{rid}.html"
    if old != new and old.exists():
        os.replace(old, new)
    metadata.touched(db, cfg, rid)  # IIIF harvesters see an Update for a published recording
    return rec.get("title"), title


# ---------- a namespace's numbers for a range of days (the report overview) ----------
def _month(day):
    return int(day[:4]), int(day[5:7])


def _months(first, last):
    """Every calendar month from `first` to `last` ((year, month) pairs), the latest MONTHS_MAX of them."""
    a, b = first[0] * 12 + first[1] - 1, last[0] * 12 + last[1] - 1
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(max(a, b - MONTHS_MAX + 1), b + 1)]


def namespace_stats(db, sid, date_from=None, date_to=None, top=5, today=None):
    """What a namespace's report shows for the recordings made from `date_from` to `date_to` (days, both included;
    either may be left open): how many and how long, who was heard and for how long (in those recordings), and the
    same per calendar month. Recordings without a date count only when there's no range; months never include them.
    Raises ValueError when the range ends before it starts."""
    lo = _day(date_from).isoformat() if date_from else None
    hi = (_day(date_to) + dt.timedelta(days=1)).isoformat() if date_to else None
    if lo and hi and lo >= hi:
        raise ValueError("the range ends before it starts")
    picked, undated = [], 0
    for r in db.rows("SELECT record::id(id) AS id, recorded_at, duration_ms FROM recording WHERE space = $s", s=sid):
        at = str(r["recorded_at"]) if r.get("recorded_at") else ""
        if not at:
            undated += 1
        if (lo or hi) and (not at or (lo and at < lo) or (hi and at >= hi)):
            continue
        picked.append((r["id"], at[:10] if DAY.match(at) else "", r.get("duration_ms") or 0))  # a day, else no month
    ids = {i for i, _, _ in picked}
    days = sorted(d for _, d, _ in picked if d)

    months = {}
    if lo or days:
        day = (today or dt.date.today()).isoformat()
        end = _day(date_to).isoformat() if date_to else max(day, days[-1] if days else day)
        months = {m: {"month": m, "recordings": 0, "ms": 0} for m in _months(_month(lo or days[0]), _month(end))}
        for _, d, ms in picked:
            if d and d[:7] in months:
                months[d[:7]]["recordings"] += 1
                months[d[:7]]["ms"] += ms

    talk, heard = Counter(), defaultdict(set)
    groups = "SELECT speaker, recording, math::sum(dur) AS ms FROM segment WHERE space = $s AND speaker > 0 GROUP BY speaker, recording"
    for g in db.rows(groups, s=sid) if ids else []:
        if g["recording"] in ids:
            talk[g["speaker"]] += g.get("ms") or 0
            heard[g["speaker"]].add(g["recording"])
    best = sorted(heard, key=lambda k: (-talk[k], k))[:top]
    names = {
        r["id"]: r.get("name") or r.get("label")
        for r in (
            db.rows("SELECT record::id(id) AS id, name, label FROM speaker WHERE id IN $ids", ids=[R("speaker", k) for k in best])
            if best
            else []
        )
    }
    return {
        "from": _day(date_from).isoformat() if date_from else None,
        "to": _day(date_to).isoformat() if date_to else None,
        "recordings": len(picked),
        "ms": sum(ms for _, _, ms in picked),
        "speakers": len(heard),
        "undated": undated,
        "first": days[0] if days else None,
        "last": days[-1] if days else None,
        "months": list(months.values()),
        "top_speakers": [{"id": k, "name": names.get(k), "talk_ms": talk[k], "recordings": len(heard[k])} for k in best],
    }
