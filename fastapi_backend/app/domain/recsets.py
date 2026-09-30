"""Saved collections: named sets of recordings, either a filter (kept up to date as recordings arrive) or a fixed list.
They scope chat, batch runs and collection reports. What a collection resolves to always depends on who is asking:
recordings in namespaces they can't read never appear."""
from __future__ import annotations

import re

from . import search as searchmod, store

R = store.R
FILTER_KEYS = ("namespaces", "speakers", "entities", "from", "to", "q", "media", "status")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_filter(f):
    f = {k: v for k, v in dict(f or {}).items() if v not in (None, "", [])}
    extra = sorted(set(f) - set(FILTER_KEYS))
    if extra:
        raise ValueError(f"collections can filter by {', '.join(FILTER_KEYS)}; not {extra[0]}")
    for k in ("from", "to"):
        if k in f and not DATE.match(str(f[k])):
            raise ValueError(f"{k} is a date like 2026-09-30")
    for k in ("speakers", "entities"):
        if k in f:
            f[k] = [int(x) for x in f[k]]
    if "namespaces" in f:
        f["namespaces"] = [str(x) for x in f["namespaces"]]
    if f.get("media") not in (None, "audio", "video", "transcript"):
        raise ValueError("media is audio, video or transcript")
    return f


def _kind(r):
    return (r.get("media") or {}).get("kind") or ("audio" if r.get("source") == "audio" else "transcript")


def resolve(db, spaces, filt=None, recordings=None, limit=None):
    """Recording ids, newest first, limited to namespaces in `spaces`."""
    names = store.space_names(db)
    f = filt or {}
    sp = {s for s in spaces if not f.get("namespaces") or names.get(s) in f["namespaces"]}
    if not sp:
        return []
    rows = {r["id"]: r for r in db.rows("SELECT record::id(id) AS id, space, recorded_at, media, source, status FROM recording WHERE space IN $s", s=sorted(sp))}
    if recordings is not None:
        return [int(i) for i in recordings if int(i) in rows][:limit or None]
    cand = set(rows)
    if f.get("from"):
        cand = {i for i in cand if (rows[i].get("recorded_at") or "")[:10] >= f["from"]}
    if f.get("to"):
        cand = {i for i in cand if (rows[i].get("recorded_at") or "")[:10] <= f["to"]}
    if f.get("media"):
        cand = {i for i in cand if _kind(rows[i]) == f["media"]}
    if f.get("status"):
        cand = {i for i in cand if rows[i].get("status") == f["status"]}
    if f.get("speakers"):
        cand &= set(db.values("SELECT VALUE recording FROM appearance WHERE speaker IN $s", s=f["speakers"]))
    if f.get("entities"):
        cand &= set(db.values("SELECT VALUE recording FROM mentions WHERE entity IN $e", e=f["entities"]))
    if f.get("q"):
        cand &= {h["recording_id"] for h in searchmod.search(db, f["q"], spaces=sp, limit=1000)["hits"]}
    return sorted(cand, key=lambda i: (rows[i].get("recorded_at") or "", i), reverse=True)[:limit or None]


def create(db, account, name, filt=None, recordings=None, description=None, shared=False):
    if not (name or "").strip():
        raise ValueError("give the collection a name")
    cid = db.next_id("saved_collection")
    db.q("CREATE $r CONTENT $d", r=R("saved_collection", cid), d=store.clean({
        "account": account, "name": name.strip()[:120], "description": description, "kind": "fixed" if recordings is not None else "filter",
        "filter": clean_filter(filt) if recordings is None else None, "recordings": [int(x) for x in recordings] if recordings is not None else None,
        "shared": bool(shared), "created_at": store.now(), "updated_at": store.now()}))
    return cid


def get(db, cid):
    c = db.one("SELECT record::id(id) AS id, account, name, description, kind, filter, recordings, shared, created_at, updated_at FROM $r",
               r=R("saved_collection", int(cid)))
    if not c:
        raise KeyError(cid)
    return c


def update(db, cid, name=None, description=None, filt=None, recordings=None, shared=None):
    c = get(db, cid)
    patch = store.clean({"name": (name or "").strip()[:120] or None, "description": description, "shared": shared, "updated_at": store.now(),
                         "filter": clean_filter(filt) if filt is not None and c["kind"] == "filter" else None,
                         "recordings": [int(x) for x in recordings] if recordings is not None and c["kind"] == "fixed" else None})
    db.q("UPDATE $r MERGE $p", r=R("saved_collection", int(cid)), p=patch)


def visible(db, account):
    return db.rows("SELECT record::id(id) AS id, account, name, description, kind, filter, recordings, shared, updated_at FROM saved_collection "
                   "WHERE account = $a OR shared = true ORDER BY updated_at DESC", a=account)


def members(db, c, spaces, limit=None):
    return resolve(db, spaces, c.get("filter"), c.get("recordings"), limit)
