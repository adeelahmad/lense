"""Content as activities (docs/automations.md, Sources): the `recordings` source reads the assets Lens keeps (the
`recording` table: audio, video, documents, images, emails, web pages) as `asset.added` activities, by `created_at`
with the id breaking ties, so nothing is stored twice.

Each one's id is `create-recording-<id>`, so it is the same every time it's read. Its actor is whoever added it: an
automation (the asset's first job was queued by `automation:<id>`), the storage source a watched file came from, the
person whose upload it was, or Lens itself (a folder scan). Its object is the asset; its title is outside text.
"""

from __future__ import annotations

import re

from . import events, store

R = store.R
FIELDS = "id AS rid, record::id(id) AS id, space, title, source, media, remote, content_type, created_at"
TYPES = {"audio": "Audio", "video": "Video", "image": "Image", "document": "Document", "transcript": "Document"}
AUTOMATION_RX = re.compile(r"^automation:(\d+)$")


def _sec(t):
    """A time as `created_at` is written (store.now(): to the second, +00:00), cut down to the second."""
    return events._parse(t).replace(microsecond=0).isoformat(timespec="seconds")


def _actors(db, rows):
    """{recording id: actor} for these rows, in a few queries for all of them."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    first_job: dict = {}
    for j in db.rows("SELECT recording, created_by, created_at FROM job WHERE recording IN $ids ORDER BY created_at", ids=ids):
        first_job.setdefault(j["recording"], j.get("created_by"))
    uploads = {u["recording"]: u for u in db.rows("SELECT recording, account, email FROM upload WHERE recording IN $ids", ids=ids)}
    accounts = {u["account"] for u in uploads.values() if isinstance(u.get("account"), int)}
    names = {
        a["id"]: a.get("name") or a.get("email")
        for a in (
            db.rows("SELECT record::id(id) AS id, name, email FROM account WHERE id IN $a", a=[R("account", n) for n in accounts])
            if accounts
            else []
        )
    }
    srcs = {(r.get("remote") or {}).get("source") for r in rows} - {None}
    sources = {
        s["id"]: s.get("name")
        for s in (
            db.rows("SELECT record::id(id) AS id, name FROM storage_source WHERE id IN $s", s=[R("storage_source", n) for n in srcs])
            if srcs
            else []
        )
    }
    out = {}
    for r in rows:
        m = AUTOMATION_RX.match(str(first_job.get(r["id"]) or ""))
        sid = (r.get("remote") or {}).get("source")
        up = uploads.get(r["id"])
        if m:
            out[r["id"]] = {"type": "Application", "id": f"lens:automation/{m.group(1)}", "name": f"Automation {m.group(1)}"}
        elif sid is not None and re.match(r"^[A-Za-z0-9_.:@+-]{1,200}$", str(sid)):
            out[r["id"]] = {"type": "Service", "id": f"lens:source/{sid}", "name": sources.get(sid) or f"Source {sid}"}
        elif up and isinstance(up.get("account"), int):
            out[r["id"]] = {
                "type": "Person",
                "id": f"lens:account/{up['account']}",
                "name": names.get(up["account"]) or up.get("email") or "Someone",
            }
        else:
            out[r["id"]] = dict(events.LENS)
    return out


def _activities(db, rows):
    """The rows as asset.added activities (a row whose created_at isn't a time is skipped)."""
    from . import render

    who = _actors(db, rows)
    out = []
    for r in rows:
        try:
            published = events.stamp(r["created_at"])
        except (TypeError, ValueError):
            continue
        media = render.kind(r)
        title = " ".join(str(r.get("title") or "").split()) or "Untitled"
        actor = who[r["id"]]
        obj = store.clean(
            {
                "type": TYPES.get(media, "Document"),
                "id": f"lens:recording/{r['id']}",
                "name": title,
                "url": f"/resources/{r['id']}",
                "media": media,
                "content_type": r.get("content_type") if isinstance(r.get("content_type"), str) else None,
            }
        )
        out.append(
            events.build(
                f"create-recording-{r['id']}",
                "asset.added",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} added “{title[:200]}”",
                space=r.get("space"),
                source="recordings",
                pos=[r["created_at"], r["id"]],
                published=published,
            )
        )
    return out


class RecordingsSource(events.Source):
    """recording rows as asset.added, read by (created_at, id)."""

    name = "recordings"
    kinds = ("asset.added",)

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        where = f"{events.after_sql('created_at', pos, p, table='recording')} AND {events.space_sql(spaces, p)}"
        rows = db.rows(f"SELECT {FIELDS} FROM recording WHERE {where} ORDER BY created_at, rid LIMIT {int(limit)}", **p)
        return _activities(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        p: dict = {}
        where = [events.space_sql(None if who.get("admin") else (who.get("spaces") or set()), p), "created_at != NONE"]
        if before is not None:
            where.append(events.after_sql("created_at", before, p, "<", table="recording"))
        if flt.get("spaces") is not None:
            p["fs"] = sorted(flt["spaces"])
            where.append("space IN $fs")
        if flt.get("since"):  # a looser bound, to the second; the stream checks each filter exactly again
            p["since"] = _sec(flt["since"])
            where.append("created_at > $since")
        if flt.get("until"):
            p["until"] = _sec(flt["until"])
            where.append("created_at <= $until")
        rows = db.rows(
            f"SELECT {FIELDS} FROM recording WHERE {' AND '.join(where)} ORDER BY created_at DESC, rid DESC LIMIT {int(limit)}", **p
        )
        return _activities(db, rows)

    def get(self, db, cfg, activity_id):
        m = re.match(r"^create-recording-(\d+)$", activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {FIELDS} FROM $r", r=R("recording", int(m.group(1))))
        got = _activities(db, [r]) if r else []
        return got[0] if got else None

    def get_many(self, db, cfg, ids):
        ns = [int(m.group(1)) for m in (re.match(r"^create-recording-(\d+)$", i) for i in ids) if m]
        if not ns:
            return []
        return _activities(db, db.rows(f"SELECT {FIELDS} FROM recording WHERE id IN $rs", rs=[R("recording", n) for n in ns]))


events.register_source(RecordingsSource())
