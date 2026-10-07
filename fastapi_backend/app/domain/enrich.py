"""Places and dates anytopdf finds, as hints for analysis (analysis.anytopdf, off by default; docs/configuration.md#anytopdf).

anytopdf (anytopdf.py), run here offline with no plugins, reads two things for a recording when this is on:
- its text (its segments, run together): the places it names, found in anytopdf's offline gazetteer (GeoNames), and
  the dates and times it gives, with their ISO value where the text pins one down ("3 March 2026", not "next Friday").
- a photo's own file: where it was taken, from its GPS fix, named offline (the nearest town). anytopdf reads GPS with
  exiftool, so where exiftool isn't installed a photo gets no place.

The places and dates are hints: analysis (analyze.py) adds them to the names its extractor found in each line, and
the graph is still the only place entities are made, so a namespace's entity setup decides what is kept. What was
found is kept on the recording as `enrichment` ({by, place, places, dates}) for its members to see. Nothing is sent
anywhere, and a failure here never stops analysis: it is said and analysis goes on without hints.
"""

from __future__ import annotations

import logging
import os
import pathlib
import tempfile

from . import anytopdf, ingest, store

log = logging.getLogger(__name__)
R = store.R
MAX_CHARS = 1_000_000  # text read for places and dates at most
MIN_CONFIDENCE = 0.5  # a place named in text below this is left out (anytopdf scores a bare city name 0.6)
# text: places and dates only (no colours, no OCR: the text is Lens's own); a photo: its GPS fix only
TEXT_ARGS = ["--no-provenance-page", "--colors", "off", "--ocr", "off", "--location", "on"]
PHOTO_ARGS = ["--no-provenance-page", "--colors", "off", "--ocr", "off", "--no-entities", "--location", "gps"]


def enabled(cfg):
    return bool((cfg.get("analysis") or {}).get("anytopdf")) and bool(anytopdf.binary(cfg))


def _notes(graph):
    """Every annotation anytopdf made, from all of its units."""
    return [a for u in graph.get("units") or [] for a in u.get("annotations") or []]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _place(a):
    """A location annotation as Lens keeps it."""
    at = a.get("attributes") or {}
    return store.clean(
        {
            "name": at.get("place") or a.get("text"),
            "city": at.get("city"),
            "region": at.get("region"),
            "country": at.get("country"),
            "country_code": at.get("country_code"),
            "lat": _num(at.get("latitude")),
            "lon": _num(at.get("longitude")),
            "km": _num(at.get("distance_km")),
            "from": "gps" if at.get("source") == "gps" else "text",
            "said": at.get("matched"),
        }
    )


def text_finds(graph):
    """From the graph of a recording's text: (places, dates), each [{...}], places named in it and dates it gives."""
    places, dates = [], []
    for a in _notes(graph):
        at = a.get("attributes") or {}
        if a.get("kind") == "location" and at.get("source") != "gps":
            conf = a.get("confidence")
            if conf is None or conf >= MIN_CONFIDENCE:
                places.append(_place(a))
        elif a.get("kind") == "timestamp" and at.get("entity") in ("date", "datetime", "time") and a.get("text"):
            dates.append(store.clean({"text": a["text"], "iso": at.get("iso"), "kind": at.get("entity")}))
    return places, dates


def photo_place(graph):
    """Where a photo was taken, from the graph of its file: the GPS place, or None."""
    for a in _notes(graph):
        if a.get("kind") == "location" and (a.get("attributes") or {}).get("source") == "gps":
            return _place(a)
    return None


def hints_for(segs, places, dates):
    """Per segment, [(name, type)]: each place and date found, in the lines that say it."""
    out = [[] for _ in segs]
    wanted = [(p.get("said") or p.get("name"), "PLACE") for p in places] + [(d["text"], "DATE") for d in dates]
    for name, typ in dict.fromkeys(w for w in wanted if w[0]):
        for k, s in enumerate(segs):
            if name in (s.get("text") or ""):
                out[k].append((name, typ))
    return out


def _text_graph(cfg, segs):
    text = " ".join(s.get("text") or "" for s in segs)[:MAX_CHARS]  # lines of one page run on
    if not text.strip():
        return None
    with tempfile.TemporaryDirectory(prefix="lens-enrich-") as tmp:
        src = pathlib.Path(tmp) / "text.txt"
        src.write_text(text, encoding="utf-8")
        return anytopdf.graph(cfg, src, TEXT_ARGS)


def _photo(db, cfg, rec, kept):
    """A photo's GPS place and the fingerprint of the file it was read from: read once per file, then kept. None for
    anything else, or a photo whose file isn't here."""
    if rec.get("source") != "image":
        return None
    fp = rec.get("fp_key") or rec.get("path")
    if kept.get("photo_fp") == fp and fp:
        return kept.get("place"), fp
    path = ingest.audio_path(db, cfg, rec)
    if not path or not os.path.exists(path):
        return None
    return photo_place(anytopdf.graph(cfg, path, PHOTO_ARGS)), fp


def enrich(db, cfg, rid, segs, say=None):
    """Read a recording's places and dates with anytopdf, keep them on it, and return the hints for its segments
    ([(name, type)] per segment); [] for each when this is off or anytopdf can't read it."""
    empty = [[] for _ in segs]
    if not enabled(cfg):
        return empty
    rec = db.one("SELECT source, path, remote, space, fp_key, enrichment FROM $r", r=R("recording", rid)) or {}
    kept = rec.get("enrichment") or {}
    try:
        g = _text_graph(cfg, segs)
        places, dates = text_finds(g) if g else ([], [])
        photo = _photo(db, cfg, rec, kept)
    except (anytopdf.Unavailable, ValueError, OSError) as e:
        log.warning("recording %s: no places or dates from anytopdf: %s", rid, e)
        if say:
            say(f"no places or dates from anytopdf: {e}")
        return empty
    place, fp = photo if photo else (None, None)
    found = store.clean(
        {
            "by": f"anytopdf {anytopdf.VERSION}",
            "place": place,
            "photo_fp": fp,
            "places": list({p["name"]: p for p in places}.values()) or None,
            "dates": list({(d["text"], d.get("iso")): d for d in dates}.values()) or None,
            "at": store.now(),
        }
    )
    db.q("UPDATE $r SET enrichment = $e", r=R("recording", rid), e=found)
    return hints_for(segs, places, dates)
