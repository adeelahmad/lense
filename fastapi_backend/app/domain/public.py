"""What visitors see (docs/access.md): a recording's public page, with the parts this visitor may use and no more.

Nobody needs to be signed in. How much of a recording someone sees comes from access.view(): people with permission
get all of it, everyone else a public recording's page, description and open parts, and a signed-in person only the
title of a restricted one ("content locked"). The route signs the media links, and only these.
"""

from __future__ import annotations

from . import access as acc, iiif, metadata as md, render, store

R = store.R
INTERNAL = ("speaker", "entity")  # workspace ids in derived metadata


def _description(meta):
    """The recording's descriptive metadata as IIIF publishes it, less the access fields and workspace ids."""
    out = {k: v for k, v in meta.items() if k not in md.COLUMNS}
    for k in ("creators", "contributors", "subjects"):
        if isinstance(out.get(k), list):
            out[k] = [{f: x for f, x in v.items() if f not in INTERNAL} if isinstance(v, dict) else v for v in out[k]]
    return out


def _any(db, table, rid):
    return bool(db.values(f"SELECT VALUE record::id(id) FROM {table} WHERE recording = $r LIMIT 1", r=rid))


def recording(db, cfg, rid, seen, a, member=False):
    """A recording's public page for someone who sees it as `seen` (access.view()), given its access (access.of()).

    `closed` lists the parts the recording has that this visitor can't use; a locked recording closes all of them.
    """
    rec = db.one("SELECT title, recorded_at, duration_ms, space, source, remote, media FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    meta = md.effective(db, cfg, rid)
    has = {
        "media": rec.get("source") == "audio" and bool(rec.get("remote") or render.has_audio(db, cfg, rid)),
        "transcript": _any(db, "segment", rid),
        "index": _any(db, "section", rid),
    }
    out = {
        "id": rid,
        "title": md.first(meta.get("label")) or rec.get("title"),
        "namespace": store.space_names(db).get(rec["space"]),
        "recorded_at": rec.get("recorded_at"),
        "duration_ms": rec.get("duration_ms"),
        "media_kind": (rec.get("media") or {}).get("kind") or ("audio" if rec.get("source") == "audio" else "transcript"),
        "view": seen,
        "access": a["access"],
        "open": a["open"],
        "featured": a["featured"],
        "member": member,
        "description": None,
        "media": None,
        "transcript": None,
        "chapters": None,
        "closed": [p for p in acc.PARTS if has[p]],
    }
    if seen == "locked":
        return out
    out["description"] = _description(meta)
    use = {p: has[p] and acc.usable(a, seen, p) for p in acc.PARTS}
    out["closed"] = [p for p in acc.PARTS if has[p] and not use[p]]
    if not any(use.values()):
        return out
    d = render.player_data(db, rid)
    if use["media"]:
        m = d.get("media") or {}
        out["media"] = {
            "url": f"{store.API}/recordings/{rid}/audio",
            "kind": m.get("kind") or "audio",
            "width": m.get("width"),
            "height": m.get("height"),
            "poster": d.get("poster"),
            "envelope": d.get("envelope"),
        }
    if use["transcript"]:
        out["transcript"] = {
            "speakers": [{"key": s["key"], "name": s["name"], "color": s["color"]} for s in d["speakers"]],
            "segments": [{"t0": s["t0"], "t1": s["t1"], "s": s["s"], "text": s["text"]} for s in d["segments"]],
            # IIIF serves the downloads, to anyone when the transcript is open to everyone
            "downloads": [{"format": k, "label": v[1], "url": f"/iiif/{rid}/transcript.{k}"} for k, v in iiif.DOWNLOADS.items()]
            if acc.is_open(a, "transcript")
            else [],
        }
    if use["index"]:
        out["chapters"] = [{"t0": s["t0"], "t1": s.get("t1"), "title": s.get("title")} for s in d["sections"]]
    return out
