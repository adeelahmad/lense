"""Word clouds, the player's data, the embeddable player page and static HTML reports."""

from __future__ import annotations

import base64
import html
import json
import math
import os
import pathlib
import re
import threading
import urllib.parse
from collections import Counter, defaultdict

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import analyze, graph as graphmod, speakers as spk, store, transcript

HERE = pathlib.Path(__file__).parent
WEB_DIR = HERE / "web"
ENV = Environment(loader=FileSystemLoader(str(HERE / "templates")), autoescape=select_autoescape(["html"]))
ENV.filters["tc"] = store.tc
AUDIO_TYPES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
    ".mp4": "video/mp4",
    ".amr": "audio/amr",
}
CLOUD_COLORS = ["#2F6690", "#C2571A", "#5B7F2B", "#7A4E9A", "#A23B5B"]


def json_script(obj):
    """JSON safe inside <script type="application/json">: no <, > or & survive, so transcript text
    such as '<!--<script>' cannot change how the page parses."""
    return (
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "recording"


def wordcloud_svg(words, width=720, height=340, label="Word cloud"):
    """Archimedean-spiral layout, no overlaps, deterministic. words: [(text, weight)] strongest first."""
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(label)}" '
        f'font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" class="wordcloud">'
    )
    if not words:
        return head + f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" fill="#8A96A3" font-size="14">No words yet</text></svg>'
    hi, lo = words[0][1], words[-1][1]
    placed, parts = [], []
    for rank, (text, w) in enumerate(words):
        frac = (w - lo) / (hi - lo) if hi > lo else 1.0
        size = 12 + 34 * frac**0.9
        tw, th = len(text) * size * 0.57 + 6, size * 1.08
        for step in range(900):
            a = step * 0.42
            x = width / 2 + 2.1 * a * math.cos(a) * 1.45 - tw / 2
            y = height / 2 + 2.1 * a * math.sin(a) * 0.8 - th / 2
            if x < 3 or y < 3 or x + tw > width - 3 or y + th > height - 3:
                continue
            if all(x + tw < px or px + pw < x or y + th < py or py + ph < y for px, py, pw, ph in placed):
                placed.append((x, y, tw, th))
                color = "currentColor" if rank < 3 else CLOUD_COLORS[rank % len(CLOUD_COLORS)]
                parts.append(
                    f'<text x="{x + 3:.1f}" y="{y + size * 0.84:.1f}" font-size="{size:.1f}" fill="{color}" '
                    f'font-weight="{600 if frac > 0.55 else 400}"><title>{html.escape(text)}</title>{html.escape(text)}</text>'
                )
                break
    return head + "".join(parts) + "</svg>"


def speaker_names(db, ids):
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {
        r["id"]: r.get("name") or r["label"]
        for r in db.rows("SELECT record::id(id) AS id, name, label FROM speaker WHERE id IN $ids", ids=[store.R("speaker", i) for i in ids])
    }


def color_of(sid):
    return store.SPEAKER_COLORS[sid % len(store.SPEAKER_COLORS)] if sid else "#9AA5B1"


def recording_stats(db, rid):
    rec = db.one("SELECT stats FROM $r", r=store.R("recording", rid))
    st = (rec or {}).get("stats") or {}
    names = speaker_names(db, [s.get("speaker_id") for s in st.get("speakers", [])])
    for s in st.get("speakers", []):  # an unattributed speaker has no speaker_id (stored stats drop None values)
        s["name"], s["color"] = names.get(s.get("speaker_id"), "Unattributed"), color_of(s.get("speaker_id"))
    return st


def _line(s):
    """A transcript line for the player; `w` holds its timed words as [c0, c1, t0, t1] when transcription gave them, and
    a document's or an image's blocks have their page (`p`, from 0) and where they are on it (`b`, [x, y, w, h])."""
    line = {
        "t0": s["t0"],
        "t1": s["t1"],
        "s": f"s{s['speaker']}" if s.get("speaker") else None,
        "text": s["text"],
        "e": s.get("emotion"),
        "v": s.get("event"),
    }
    if s.get("page") is not None:
        line.update(p=s["page"], b=s.get("box"))
    w = transcript.align(s["text"], transcript.words(s))
    return {**line, "w": w} if w else line


def player_data(db, rid, audio=None):
    rec = db.one("SELECT * FROM $r", r=store.R("recording", rid))
    if not rec:
        raise KeyError(rid)
    segs = db.rows(
        "SELECT record::id(id) AS id, idx, t0, t1, speaker, text, emotion, event, words, page, box FROM segment WHERE recording = $r "
        "ORDER BY idx",
        r=rid,
    )
    order = list(dict.fromkeys(s["speaker"] for s in segs if s.get("speaker")))
    names = speaker_names(db, order)
    ments = db.rows("SELECT entity, text, record::id(in) AS seg FROM mentions WHERE recording = $r", r=rid)
    types = (
        {
            x["id"]: x["type"]
            for x in db.rows(
                "SELECT record::id(id) AS id, type FROM entity WHERE id IN $ids AND hidden != true",
                ids=[store.R("entity", i) for i in {m["entity"] for m in ments}],
            )
        }
        if ments
        else {}
    )
    ents = {}
    for m in ments:
        if types.get(m["entity"]) in (None, "NUMBER", "DATE"):
            continue
        d = ents.setdefault(m["entity"], {"type": types[m["entity"]], "names": Counter(), "segs": set()})
        d["names"][m["text"]] += 1
        d["segs"].add(m["seg"] % store.SEG)
    entities = sorted(
        (
            {"name": max(d["names"].items(), key=lambda kv: (kv[1], len(kv[0])))[0], "type": d["type"], "segs": sorted(d["segs"])}
            for d in ents.values()
        ),
        key=lambda x: (-len(x["segs"]), x["name"]),
    )[:80]
    space = db.one("SELECT name FROM $r", r=store.R("space", rec["space"])) or {}
    env = rec.get("envelope")
    return {
        "id": rid,
        "title": rec.get("title"),
        "namespace": space.get("name"),
        "recorded_at": rec.get("recorded_at"),
        "duration_ms": rec.get("duration_ms") or (segs[-1]["t1"] if segs else 0),
        "audio": audio,
        "speakers": [{"key": f"s{i}", "id": i, "name": names.get(i, f"Speaker {i}"), "color": color_of(i)} for i in order],
        "segments": [_line(s) for s in segs],
        "sections": db.rows("SELECT idx, seg0, seg1, t0, t1, title FROM section WHERE recording = $r ORDER BY idx", r=rid),
        "entities": entities,
        "keywords": analyze.keywords(db, rid, 40) if rec.get("analyzed_at") else [],
        "envelope": list(env) if isinstance(env, (bytes, bytearray)) else env,
        "summary": rec.get("summary"),
        "labels": store.labels(),
        **visual(db, rid, rec),
    }


def kind(rec):
    """What a resource is: audio, video, transcript (text without media), document or image."""
    src = rec.get("source")
    if src in ("document", "image"):
        return src
    if src != "audio":
        return "transcript"
    return "video" if (rec.get("media") or {}).get("kind") == "video" else "audio"


def frame_link(rid, name):
    return f"{store.API}/recordings/{rid}/frames/{name}" if name else None


def visual(db, rid, rec):
    """Shots, text on screen and people on screen for a video recording; the pages of a document or an image."""
    media = rec.get("media") or {}
    k = kind(rec)
    out = {
        "media": store.clean(
            {
                "kind": "audio" if k == "transcript" else k,
                "width": media.get("width"),
                "height": media.get("height"),
                "fps": media.get("fps"),
                "pages": media.get("pages"),
            }
        )
    }
    if k in ("document", "image"):
        from . import documents

        out["pages"] = [
            {**p, "image": frame_link(rid, p.get("image")), "thumb": frame_link(rid, p.get("thumb"))} for p in documents.pages(db, rid)
        ]
        out["poster"] = out["pages"][0]["thumb"] if out["pages"] else None
        return {**out, **_faces(db, rid, rec), **_objects(db, rid)}  # their spans and boxes count pages, from 0
    if media.get("kind") != "video":
        return out

    frame = lambda name: frame_link(rid, name)  # noqa: E731
    out["shots"] = [
        {"idx": x["idx"], "t0": x["t0"], "t1": x["t1"], "frame": frame(x.get("frame"))}
        for x in db.rows("SELECT idx, t0, t1, frame FROM shot WHERE recording = $r ORDER BY idx", r=rid)
    ]
    out["screen_text"] = [
        {
            "id": x["id"],
            "t0": x["t0"],
            "t1": x["t1"],
            "text": x["text"],
            "box": x.get("box"),
            "frame": frame(x.get("frame")),
            "edited": bool(x.get("edited")),
        }
        for x in db.rows(
            "SELECT record::id(id) AS id, t0, t1, text, box, frame, edited FROM ocr_span WHERE recording = $r ORDER BY t0", r=rid
        )
    ]
    out.update(_faces(db, rid, rec))
    out.update(_objects(db, rid))
    out["poster"] = out["shots"][0]["frame"] if out["shots"] else None
    return out


def _faces(db, rid, rec):
    """The namespace's face mode, and the faces found in the recording (none while it's off)."""
    from . import faces

    mode = faces.mode(db, rec["space"])
    keep = ("id", "local", "face", "name", "spans", "screen_ms", "first_ms", "boxes", "score", "match")
    tracks = faces.tracks_for(db, rid) if mode != "off" else []
    return {"faces_mode": mode, "faces": [{**{k: t.get(k) for k in keep}, "cover": frame_link(rid, t.get("cover"))} for t in tracks]}


def _objects(db, rid):
    """The kinds of object found in the recording, the most seen first, each with the frame it's best seen on."""
    from . import objects

    return {"objects": [{**t, "frame": frame_link(rid, t.get("frame"))} for t in objects.for_recording(db, rid)]}


def has_audio(db, cfg, rid):
    r = db.one("SELECT path, source FROM $r", r=store.R("recording", rid)) or {}
    path = store.resolve_path(cfg, r.get("path"))
    return path if r.get("source") == "audio" and path and os.path.exists(path) else None


def embed_page(db, cfg, rid, start=0.0, audio_url=None, played=None):
    """The embeddable player. `played`: where the page reports its first play (share links only)."""
    d = player_data(db, rid, audio_url or f"{store.API}/recordings/{rid}/audio")
    if not audio_url and not has_audio(db, cfg, rid):
        d["audio"] = None
    return ENV.get_template("embed.html").render(d=d, data=json_script(d), start=float(start or 0), played=played)


def link_gone_page():
    """What an expired, revoked or mistyped link shows: nothing about the recording, not even whether it exists."""
    return ENV.get_template("link_gone.html").render()


def _assets():
    return {
        "player_css": (HERE / "web" / "player.css").read_text(encoding="utf-8"),
        "player_js": (HERE / "web" / "player.js").read_text(encoding="utf-8"),
    }


def graph_svg(g, width=900, height=520, labels=28):
    if not g["nodes"]:
        return ""
    pos = {n["id"]: ((n["x"] + 1) / 2 * (width - 120) + 60, (n["y"] + 1) / 2 * (height - 60) + 30) for n in g["nodes"]}
    wmax = max((n["weight"] for n in g["nodes"]), default=1) or 1
    emax = max((e["w"] for e in g["edges"]), default=1) or 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Knowledge graph" '
        f'font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" class="graph">'
    ]
    for e in g["edges"]:
        (x1, y1), (x2, y2) = pos[e["a"]], pos[e["b"]]
        dash = ' stroke-dasharray="4 3"' if e["kind"] in ("same person", "maybe the same voice") else ""
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#8A96A3" '
            f'stroke-opacity="{0.18 + 0.5 * e["w"] / emax:.2f}" stroke-width="{0.6 + 2 * e["w"] / emax:.2f}"{dash}/>'
        )
    top = {n["id"] for n in sorted(g["nodes"], key=lambda n: (n["kind"] != "speaker", -n["weight"]))[:labels]}
    for n in g["nodes"]:
        x, y = pos[n["id"]]
        r = 4 + 10 * math.sqrt(n["weight"] / wmax)
        if n["kind"] == "speaker":
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 2:.1f}" fill="#15202B" stroke="#FF5A1F" stroke-width="2"><title>{html.escape(n["label"])}</title></circle>'
            )
        else:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{graphmod.TYPE_COLORS.get(n.get("type"), "#6B7A89")}" '
                f'fill-opacity="0.85"><title>{html.escape(n["label"])} ({n.get("type", "").lower()})</title></circle>'
            )
        if n["id"] in top:
            parts.append(
                f'<text x="{x + r + 4:.1f}" y="{y + 4:.1f}" font-size="{12 if n["kind"] == "speaker" else 11}" '
                f'font-weight="{600 if n["kind"] == "speaker" else 400}" fill="currentColor">{html.escape(n["label"][:28])}</text>'
            )
    return "".join(parts) + "</svg>"


def report_recording(db, cfg, rid, out_dir, audio_mode="link"):
    d = player_data(db, rid)
    out = pathlib.Path(out_dir) / f"{slug(d['title'])}-{rid}.html"
    d["audio_local"], d["audio_api"] = None, None
    path = has_audio(db, cfg, rid)
    if path:
        d["audio_api"] = f"{store.API}/recordings/{rid}/audio"
        if audio_mode == "link":
            d["audio_local"] = urllib.parse.quote(os.path.relpath(path, out.parent).replace(os.sep, "/"))
        elif audio_mode == "embed":
            mime = AUDIO_TYPES.get(pathlib.Path(path).suffix.lower(), "audio/mpeg")
            d["audio_local"] = f"data:{mime};base64," + base64.b64encode(pathlib.Path(path).read_bytes()).decode()
    cloud = wordcloud_svg(analyze.keywords(db, rid, 60), label=f"Word cloud for {d['title']}")
    page = ENV.get_template("report_recording.html").render(
        d=d, data=json_script(d), stats=recording_stats(db, rid), cloud=cloud, generated=store.now(), **_assets()
    )
    out.write_text(page, encoding="utf-8")
    return out


def report_namespace(db, cfg, nid, out_dir, links):
    ns = db.one("SELECT name, graph FROM $r", r=store.R("space", nid))
    recs = db.rows(
        "SELECT record::id(id) AS id, title, recorded_at, duration_ms, status, summary FROM recording WHERE space = $s "
        "ORDER BY recorded_at DESC",
        s=nid,
    )
    apps = defaultdict(list)
    for a in db.rows("SELECT recording, speaker FROM appearance WHERE space = $s", s=nid):
        apps[a["recording"]].append(a["speaker"])
    names = speaker_names(db, [x for v in apps.values() for x in v])
    for r in recs:
        r["speakers"] = ", ".join(dict.fromkeys(names.get(x, "?") for x in apps.get(r["id"], [])))
        r["link"] = links.get(r["id"])
    months = Counter((r.get("recorded_at") or "")[:7] for r in recs if r.get("recorded_at"))
    mx = max(months.values(), default=1)
    counts = {r["entity"]: r["n"] for r in db.rows("SELECT entity, count() AS n FROM mentions WHERE space = $s GROUP BY entity", s=nid)}
    recs_per = Counter(
        r["entity"] for r in db.rows("SELECT entity, recording FROM mentions WHERE space = $s GROUP BY entity, recording", s=nid)
    )
    shown = {}
    for r in db.rows("SELECT entity, text, count() AS n FROM mentions WHERE space = $s GROUP BY entity, text", s=nid):
        if r["entity"] not in shown or (r["n"], len(r["text"])) > shown[r["entity"]][1]:
            shown[r["entity"]] = (r["text"], (r["n"], len(r["text"])))
    ents = [
        {"name": shown.get(e["id"], (e["key"],))[0], "type": e["type"], "n": counts.get(e["id"], 0), "recs": recs_per.get(e["id"], 0)}
        for e in db.rows("SELECT record::id(id) AS id, key, type FROM entity WHERE space = $s AND type NOT IN ['NUMBER', 'DATE']", s=nid)
    ]
    ents = sorted((e for e in ents if e["n"]), key=lambda e: (-e["recs"], -e["n"]))[:40]
    g = graphmod.build(db, cfg, "ns:" + ns["name"])
    page = ENV.get_template("report_namespace.html").render(
        ns=ns,
        recs=recs,
        speakers=spk.list_speakers(db, nid)[:30],
        entities=ents,
        months=[{"m": m, "n": n, "pct": 100 * n / mx} for m, n in sorted(months.items())],
        total_ms=sum(r.get("duration_ms") or 0 for r in recs),
        cloud=wordcloud_svg(analyze.ns_keywords(db, nid, 80), label=f"Word cloud for {ns['name']}"),
        graph=graph_svg(g),
        graph_n=len(g["nodes"]),
        generated=store.now(),
    )
    out = pathlib.Path(out_dir) / "index.html"
    out.write_text(page, encoding="utf-8")
    return out


def _links(out_dir, links=None):
    """The recording report pages in a namespace's folder: {recording id: file name}."""
    links = links if links is not None else {}
    for p in out_dir.glob("*-*.html"):
        m = re.search(r"-(\d+)\.html$", p.name)
        if m:
            links.setdefault(int(m.group(1)), p.name)
    return links


def build_reports(db, cfg, ns=None, rid=None, audio_mode=None, log=print):
    base, mode, written = pathlib.Path(cfg["data_dir"]) / "reports", audio_mode or cfg["reports"]["audio"], []
    spaces = db.rows("SELECT record::id(id) AS id, name FROM space" + (" WHERE name = $n" if ns else ""), n=ns)
    for n in spaces:
        out_dir = base / n["name"]
        out_dir.mkdir(parents=True, exist_ok=True)
        links = {}
        q = "SELECT record::id(id) AS id FROM recording WHERE space = $s AND analyzed_at != NONE" + (" AND id = $r" if rid else "")
        for r in db.rows(q, s=n["id"], r=store.R("recording", rid) if rid else None):
            p = report_recording(db, cfg, r["id"], out_dir, mode)
            links[r["id"]] = p.name
            written.append(p)
        _links(out_dir, links)
        if links:
            written.append(report_namespace(db, cfg, n["id"], out_dir, links))
            log(f"  {n['name']}: {len(links)} recording report(s) and an overview")
    return written


_OVERVIEWS: dict[str, bool] = {}  # namespace -> asked again while its overview was being rewritten
_OL = threading.Lock()


def refresh_overview(db, cfg, ns):
    """Rewrite a namespace's report overview (index.html) after recordings went away, when it has one. Calls that come
    while a rewrite runs make it run once more, instead of running alongside it."""
    with _OL:
        if ns in _OVERVIEWS:
            _OVERVIEWS[ns] = True
            return
        _OVERVIEWS[ns] = False
    try:
        while True:
            out_dir = pathlib.Path(cfg["data_dir"]) / "reports" / ns
            row = db.one("SELECT record::id(id) AS id FROM space WHERE name = $n", n=ns)
            if row and (out_dir / "index.html").exists():
                report_namespace(db, cfg, row["id"], out_dir, _links(out_dir))
            with _OL:
                if not _OVERVIEWS[ns]:
                    del _OVERVIEWS[ns]
                    return
                _OVERVIEWS[ns] = False
    except BaseException:
        with _OL:
            _OVERVIEWS.pop(ns, None)
        raise


def _ts(ms, sep):
    h, rem = divmod(int(ms or 0), 3600000)
    m, rem = divmod(rem, 60000)
    sec, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}{sep}{milli:03d}"


def export_text(d, fmt):
    """A recording's transcript as txt, md, srt, vtt or json (lens/1 JSON imports straight back in)."""
    names = {sp["key"]: sp["name"] for sp in d["speakers"]}
    segs = [
        {"t0": x["t0"], "t1": x["t1"], "speaker": names.get(x["s"]), "text": x["text"], "emotion": x.get("e"), "event": x.get("v")}
        for x in d["segments"]
    ]
    if fmt == "json":
        return json.dumps({"lens": "lens/1", "doc": {"title": d["title"], "speakers": {}}, "segments": segs}, ensure_ascii=False, indent=1)
    if fmt == "txt":
        return "".join(f"[{store.tc(x['t0'])}] {x['speaker'] or 'Unknown'}: {x['text']}\n" for x in segs)
    if fmt == "md":
        return f"# {d['title']}\n\n" + "".join(f"**{x['speaker'] or 'Unknown'}** ({store.tc(x['t0'])}): {x['text']}\n\n" for x in segs)
    if fmt == "srt":
        return "\n".join(
            f"{i}\n{_ts(x['t0'], ',')} --> {_ts(x['t1'], ',')}\n{(x['speaker'] + ': ') if x['speaker'] else ''}{x['text']}\n"
            for i, x in enumerate(segs, 1)
        )
    return "WEBVTT\n\n" + "\n".join(
        f"{_ts(x['t0'], '.')} --> {_ts(x['t1'], '.')}\n{('<v ' + x['speaker'] + '>') if x['speaker'] else ''}{x['text']}\n" for x in segs
    )
