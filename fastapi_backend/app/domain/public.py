"""What visitors see (docs/access.md): the home page, collections, and a recording's public page, with what this
visitor may see and no more.

Nobody needs to be signed in. How much of a recording someone sees comes from access.view(): people with permission
get all of it, everyone else a public recording's page, description and open parts, and a signed-in person only the
title of a restricted one ("content locked"). The routes sign the media links, and only these.
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


# ---------- lists: the home page's featured recordings, collections and their pages ----------
CARD = "record::id(id) AS id, title, recorded_at, duration_ms, space, source, media, summary, meta_json, access, access_parts, featured"


def listed(db, sid, member, signed_in):
    """The WHERE clause (and its parameters) for the recordings of one namespace that someone sees listed, i.e. whose
    access.view() isn't None: all of them for members, else public ones, and restricted ones when signed in."""
    if member:
        return "space = $s", {"s": sid}
    levels = ["public", "restricted"] if signed_in else ["public"]
    default, _ = acc.namespace_defaults(db, [sid]).get(sid, ("private", None))
    follows = " OR access = NONE" if default in levels else ""  # recordings without their own setting follow the namespace
    return f"space = $s AND (access IN $lv{follows})", {"s": sid, "lv": levels}


def _count(db, cond, p):
    return db.values(f"RETURN array::len((SELECT VALUE id FROM recording WHERE {cond}))", **p)[0]


def cards(db, rows, member_of, signed_in):
    """Recording rows (CARD fields) as cards for this visitor: a locked card has no summary, and a poster frame only
    comes with media the visitor may play."""
    access = acc.many(db, rows)
    names = store.space_names(db)
    ids = [r["id"] for r in rows]
    posters = (
        {x["recording"]: x.get("frame") for x in db.rows("SELECT recording, frame FROM shot WHERE recording IN $r AND idx = 0", r=ids)}
        if rows
        else {}
    )
    out = []
    for r in rows:
        a = access[r["id"]]
        seen = acc.view(a, r["space"] in member_of, signed_in)
        if seen is None:
            continue
        meta = md._load(r.get("meta_json"))
        own = r.get("summary") or {}
        frame = posters.get(r["id"])
        out.append(
            {
                "id": r["id"],
                "title": md.first(meta.get("label")) or r.get("title"),
                "namespace": names.get(r["space"]),
                "recorded_at": r.get("recorded_at"),
                "duration_ms": r.get("duration_ms"),
                "media_kind": (r.get("media") or {}).get("kind") or ("audio" if r.get("source") == "audio" else "transcript"),
                "view": seen,
                "access": a["access"],
                "featured": a["featured"],
                "summary": None if seen == "locked" else (md.first(meta.get("summary")) or own.get("tldr") or own.get("summary")),
                "poster": f"{store.API}/recordings/{r['id']}/frames/{frame}" if frame and acc.usable(a, seen, "media") else None,
            }
        )
    return out


def _collection_meta(db, sid):
    ns = md.namespace(db, sid)
    meta = ns["meta"]
    return {
        "name": ns["name"],
        "label": md.first(meta.get("label")) or ns["name"],
        "summary": md.first(meta.get("summary")) or None,
        "rights": meta.get("rights"),
        "attribution": md.first(meta.get("attribution")) or None,
        "provider": meta.get("provider"),
    }


def home(db, member_of, signed_in, featured_limit=12):
    """The public home page: featured public recordings (for everyone, members too) and the collections this visitor
    sees anything in, with how many recordings they see there."""
    public_spaces = [sid for sid, (level, _) in acc.namespace_defaults(db).items() if level == "public"]
    rows = db.rows(
        f"SELECT {CARD} FROM recording WHERE featured = true AND (access = 'public' OR (access = NONE AND space IN $pub)) "
        "ORDER BY recorded_at DESC LIMIT $n",
        pub=public_spaces,
        n=featured_limit,
    )
    collections = []
    for sid, name in sorted(store.space_names(db).items(), key=lambda x: x[1]):
        cond, p = listed(db, sid, sid in member_of, signed_in)
        n = _count(db, cond, p)
        if n:
            c = _collection_meta(db, sid)
            collections.append({"name": name, "label": c["label"], "summary": c["summary"], "recordings": n, "member": sid in member_of})
    return {"featured": cards(db, rows, member_of, signed_in), "collections": collections}


def collection(db, sid, member_of, signed_in, limit=48, offset=0):
    """A collection's page: the namespace's description and the recordings this visitor sees listed, newest first.
    Raises KeyError when they see none and aren't a member, so the namespace looks absent."""
    member = sid in member_of
    cond, p = listed(db, sid, member, signed_in)
    total = _count(db, cond, p)
    if not total and not member:
        raise KeyError(sid)
    rows = db.rows(f"SELECT {CARD} FROM recording WHERE {cond} ORDER BY recorded_at DESC LIMIT $n START $o", **p, n=limit, o=offset)
    return {**_collection_meta(db, sid), "member": member, "total": total, "items": cards(db, rows, member_of, signed_in)}
