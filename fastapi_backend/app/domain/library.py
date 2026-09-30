"""The library: recordings listed with filters, sorting and a total count, for everyone who can read them."""

from __future__ import annotations

import datetime as dt
import os
import pathlib
from collections import defaultdict

from . import metadata, render, store

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
FIELDS = "record::id(id) AS id, title, recorded_at, duration_ms, status, error, source, stats, summary, space, media"
MAX_WORDS = 10


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
):
    """The WHERE clause and its parameters for these filters. Filters combine with AND, the values of one filter with OR.

    q: every word in the title, the namespace's name or a speaker's name. status: recording statuses and job states
    (STATES). attention: errored, latest job failed, or a voice match to review. processing: a job queued or running.
    speakers: speaker ids. date_from/date_to: the recording date, inclusive. min/max_duration: seconds, max exclusive.
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
    return " AND ".join(w), p


def _speakers(db, spaces):
    rows = db.rows("SELECT record::id(id) AS id, name, label FROM speaker WHERE space IN $s", s=spaces)
    return [{"id": r["id"], "display": (r.get("name") or r.get("label") or "").lower()} for r in rows]


def list_recordings(db, spaces, sort="-date", limit=500, offset=0, **filters):
    """(one page of recording summaries, how many match in all). `sort` is a key of SORTS, prefixed with - for descending."""
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
    return summaries(db, rows), int(total[0]) if total else 0


def summaries(db, rows):
    """List rows as the library shows them: namespace, speakers, media kind, poster frame, emotion mix and summary."""
    ids = [r["id"] for r in rows]
    apps = defaultdict(list)
    for a in db.rows("SELECT recording, speaker FROM appearance WHERE recording IN $r", r=ids) if rows else []:
        apps[a["recording"]].append(a["speaker"])
    names, spaces = render.speaker_names(db, [x for v in apps.values() for x in v]), store.space_names(db)
    posters = (
        {x["recording"]: x.get("frame") for x in db.rows("SELECT recording, frame FROM shot WHERE recording IN $r AND idx = 0", r=ids)}
        if rows
        else {}
    )
    out = []
    for r in rows:
        for k in ("_k", "_none"):
            r.pop(k, None)
        st, sm = r.pop("stats", None) or {}, r.pop("summary", None) or {}
        r["media_kind"] = (r.pop("media", None) or {}).get("kind") or ("audio" if r.get("source") == "audio" else "transcript")
        r["poster"] = f"{store.API}/recordings/{r['id']}/frames/{posters[r['id']]}" if posters.get(r["id"]) else None
        out.append(
            {
                **r,
                "namespace": spaces.get(r["space"]),
                "emotions": st.get("emotions", {}),
                "words": st.get("words"),
                "importance": sm.get("importance"),
                "sentiment": sm.get("sentiment"),
                "speakers": ",".join(dict.fromkeys(names.get(x, "?") for x in apps.get(r["id"], []))),
            }
        )
    return out


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
