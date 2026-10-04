"""Daily digests: a stream sensor whose handling says `digest` writes, for each full day (UTC), a document in its
namespace that says what it reported: each stream's count and range, its JSON fields' ranges, the busiest log
patterns with their labels, and the warnings and errors. The document is a recording like any other, queued for the
namespace's pipeline, so it can be searched, linked and asked about. Days are written once, oldest first, at most
MAX_DAYS a run; a day with nothing in it is skipped.
"""

from __future__ import annotations

import datetime as dt

from . import ingest, jobs, sensors, store

R = store.R
MAX_DAYS = 7
TOP_PATTERNS, MAX_WARNINGS = 20, 30


def _num(v):
    if v is None:
        return "–"
    return f"{v:.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:.2f}"


def text(db, s, day):
    """The digest of `day` (a date) for sensor `s`, as Markdown; None when nothing arrived that day."""
    a = dt.datetime.combine(day, dt.time(), dt.timezone.utc)
    b = a + dt.timedelta(days=1)
    h0, h1 = a.isoformat(timespec="hours")[:13], b.isoformat(timespec="hours")[:13]
    sts = {x["id"]: x for x in sensors.streams(db, s["id"])}
    rolls = db.rows(
        "SELECT stream, field, math::sum(n) AS n, math::min(min) AS min, math::max(max) AS max, math::sum(sum) AS sum "
        "FROM sensor_rollup WHERE sensor = $s AND hour >= $a AND hour < $b GROUP BY stream, field",
        s=s["id"],
        a=h0,
        b=h1,
    )
    if not rolls:
        return None
    lines = [f"# {s['name']}: {day.isoformat()}", ""]
    lines.append(f"What {s['name']} ({sensors.STREAM_TYPES[s['type']]['label'].lower()}) reported on {day.isoformat()} (UTC).")
    lines += ["", "## Streams", ""]
    by_stream = {}
    for r in rolls:
        by_stream.setdefault(r["stream"], []).append(r)
    for k, rs in sorted(by_stream.items(), key=lambda kv: (sts.get(kv[0]) or {}).get("name") or ""):
        st = sts.get(k) or {}
        name, kind = st.get("name") or k, st.get("kind") or "?"
        main = next((r for r in rs if not r.get("field")), None)
        n = int((main or rs[0]).get("n") or 0)
        if kind in ("number", "boolean") and main and main.get("sum") is not None:
            avg = main["sum"] / n if n else None
            lines.append(f"- {name}: {n} readings, from {_num(main.get('min'))} to {_num(main.get('max'))}, averaging {_num(avg)}")
        else:
            lines.append(f"- {name} ({kind}): {n} {'lines' if kind in ('log', 'text') else 'readings'}")
        for r in sorted((r for r in rs if r.get("field")), key=lambda r: r["field"]):
            avg = r["sum"] / r["n"] if r.get("n") and r.get("sum") is not None else None
            lines.append(f"  - {r['field']}: from {_num(r.get('min'))} to {_num(r.get('max'))}, averaging {_num(avg)}")
    a_ts, b_ts = sensors._ts(a), sensors._ts(b)
    counts = db.rows(
        "SELECT pattern, count() AS n FROM sensor_reading WHERE sensor = $s AND at >= $a AND at < $b AND pattern != NONE GROUP BY pattern",
        s=s["id"],
        a=a_ts,
        b=b_ts,
    )
    if counts:
        counts = sorted(counts, key=lambda c: -c["n"])[:TOP_PATTERNS]
        pats = {
            p["id"]: p
            for p in db.rows(
                "SELECT record::id(id) AS id, template, label FROM sensor_pattern WHERE record::id(id) IN $ids",
                ids=[c["pattern"] for c in counts],
            )
        }
        lines += ["", "## Busiest kinds of log line", ""]
        for c in counts:
            p = pats.get(c["pattern"]) or {}
            label = f" ({p['label']})" if p.get("label") else ""
            lines.append(f"- {c['n']}×{label} `{(p.get('template') or c['pattern']).replace('`', "'")}`")
    warn = db.rows(
        "SELECT at, text, level, stream FROM sensor_reading WHERE sensor = $s AND at >= $a AND at < $b AND level != NONE "
        "AND level <= $lv ORDER BY at LIMIT $n",
        s=s["id"],
        a=a_ts,
        b=b_ts,
        lv=sensors.IMPORTANT_LEVEL,
        n=MAX_WARNINGS,
    )
    if warn:
        from .syslog import SEVERITIES

        lines += ["", "## Warnings and errors", ""]
        for w in warn:
            prog = (sts.get(w.get("stream")) or {}).get("name") or ""
            lines.append(f"- {w['at'][11:19]} {SEVERITIES[w['level']]} {prog}: {(w.get('text') or '')[:300]}")
    return "\n".join(lines) + "\n"


def write(db, cfg, ids=None, today=None, say=None):
    """Write the days not yet written for every sensor whose handling says digest: how many documents."""
    say = say or (lambda *a: None)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    names = store.space_names(db)
    made = 0
    for s in db.rows(f"SELECT {sensors.FIELDS}, digest_until FROM storage_source WHERE type IN $t", t=list(sensors.STREAM_TYPES)):
        if (ids is not None and s["id"] not in ids) or not sensors.handling(cfg, s).get("digest"):
            continue
        ns = names.get(s.get("space"))
        if not ns:
            say(f"{s['name']}: a digest needs a namespace")
            continue
        start = s.get("digest_until") or (s.get("created_at") or store.now())[:10]
        day = dt.date.fromisoformat(start)
        n = 0
        while day < today and n < MAX_DAYS:
            body = text(db, s, day)
            if body:
                rid = ingest.import_text(db, cfg, ns, body, f"{s['name']}: {day.isoformat()}", "text", name=f"{day.isoformat()}.txt")
                db.q(
                    "UPDATE $r MERGE $d",
                    r=R("recording", rid),
                    d={"recorded_at": f"{day.isoformat()}T23:59:59+00:00", "sensor": s["id"]},
                )
                jobs.enqueue(db, rid, None, by=f"sensor:{s['id']}")
                made += 1
            day += dt.timedelta(days=1)
            n += 1
            db.q("UPDATE $r SET digest_until = $d", r=R("storage_source", s["id"]), d=day.isoformat())
    if made:
        say(f"wrote {made} daily digests")
    return made
