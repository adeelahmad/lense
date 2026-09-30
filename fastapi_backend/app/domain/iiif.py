"""IIIF: each recording is a Presentation 3.0 Manifest, each namespace a Collection. Also Content Search 2.0,
Content State 1.0 links, Change Discovery 1.0 and importing IIIF audio published elsewhere.

What a Manifest carries depends on the recording's access (see metadata.ACCESS): open resources are plain links;
protected ones carry IIIF Authorization Flow 2.0 probe services (see iiif_auth), and transcript layers are only
published when the transcript is open.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import pathlib
import re
import time
import urllib.parse
import urllib.request
from collections import Counter

from . import ingest, metadata as md, pipelines, render, settings, speakers as spk, store

R = store.R
P3 = "http://iiif.io/api/presentation/3/context.json"
SEARCH2 = "http://iiif.io/api/search/2/context.json"
AUTH2 = "http://iiif.io/api/auth/2/context.json"
DISCOVERY1 = "http://iiif.io/api/discovery/1/context.json"
JSONLD = 'application/ld+json;profile="http://iiif.io/api/presentation/3/context.json"'
OPEN_TRANSCRIPT = ("public", "transcript")
LAYERS = {"transcript": "Transcript", "speakers": "Speakers", "entities": "People, places and things mentioned", "screen": "Text on screen",
          "faces": "People on screen"}
DOWNLOADS = {"vtt": ("text/vtt", "Transcript (WebVTT)"), "srt": ("application/x-subrip", "Transcript (SRT)"),
             "txt": ("text/plain", "Transcript (plain text)"), "md": ("text/markdown", "Transcript (Markdown)"), "json": ("application/json", "Transcript (JSON)")}
PAGE = 100


def lm(text, lang="none"):
    return {lang or "none": [str(text)]}


def _t(ms):
    return f"{(ms or 0) / 1000:.3f}".rstrip("0").rstrip(".")


def _lang(rec):
    x = (rec or {}).get("language")
    return x if x and md.LANG_RX.match(x) and x not in ("none", "nospeech") else None


def _prune(d):
    return {k: v for k, v in d.items() if v not in (None, [], {})}


def site_label(cfg, base):
    return (cfg["iiif"].get("provider") or {}).get("name") or urllib.parse.urlsplit(base).netloc or "Lens Archive"


def probe_service(cfg, base, rid, what):
    site = site_label(cfg, base)
    return {"id": f"{base}/iiif/auth/probe/{rid}/{what}", "type": "AuthProbeService2",
            "service": [{"id": f"{base}/iiif/auth/access", "type": "AuthAccessService2", "profile": "active",
                         "label": lm(f"Sign in to {site}", "en"), "heading": lm("Sign in to listen", "en"),
                         "note": lm(f"{site} requires an account with access to this collection.", "en"), "confirmLabel": lm("Sign in", "en"),
                         "service": [{"id": f"{base}/iiif/auth/token", "type": "AuthAccessTokenService2"},
                                     {"id": f"{base}/iiif/auth/logout", "type": "AuthLogoutService2", "label": lm(f"Sign out of {site}", "en")}]}]}


def _pairs(meta, rec, ns):
    """The label/value pairs viewers show."""
    names = lambda xs: ", ".join(p["name"] for p in xs or [])  # noqa: E731
    out = list(meta.get("metadata") or [])
    for label, value in (("Date", (meta.get("navDate") or "")[:10]), ("Duration", store.tc(rec.get("duration_ms")) if rec.get("duration_ms") else None),
                         ("Language", ", ".join(meta.get("language") or [])), ("Creators", names(meta.get("creators"))),
                         ("Speakers", names(meta.get("contributors"))), ("Subjects", ", ".join(s["label"] for s in meta.get("subjects") or [])),
                         ("Identifiers", ", ".join(i["value"] for i in meta.get("identifiers") or [])), ("Collection", ns)):
        if value:
            out.append({"label": lm(label, "en"), "value": lm(value)})
    return out


def _provider(meta, base):
    p = meta.get("provider")
    if not p:
        return None
    agent = {"id": p.get("homepage") or f"{base}/#provider", "type": "Agent", "label": lm(p["name"])}
    if p.get("homepage"):
        agent["homepage"] = [{"id": p["homepage"], "type": "Text", "label": lm(p["name"]), "format": "text/html"}]
    if p.get("logo"):
        agent["logo"] = [{"id": p["logo"], "type": "Image", "format": "image/svg+xml" if p["logo"].endswith(".svg") else "image/png"}]
    return [agent]


def manifest(db, cfg, rid, base):
    rec = db.one("SELECT * FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    meta = md.effective(db, cfg, rid)
    level = meta.get("access") or "private"
    ns = (db.one("SELECT name FROM $s", s=R("space", rec["space"])) or {}).get("name")
    d = render.player_data(db, rid)
    m, lang, tlang = f"{base}/iiif/{rid}", cfg["iiif"].get("default_language") or "none", _lang(rec)
    canvas, dur = f"{m}/canvas/1", max(round((d["duration_ms"] or 0) / 1000, 3), 0.001)
    layers = set(cfg["iiif"].get("layers") or [])
    audio_locked, text_locked = level != "public", level not in OPEN_TRANSCRIPT
    has_audio = rec.get("source") == "audio" and bool(rec.get("remote") or render.has_audio(db, cfg, rid))
    title = md.first(meta.get("label")) or d["title"]
    media = rec.get("media") or {}
    is_video = media.get("kind") == "video" and media.get("width")
    items = []
    if has_audio:
        ext = pathlib.PurePosixPath((rec.get("remote") or {}).get("path") or rec.get("path") or "").suffix.lower()
        body = {"id": f"{m}/audio", "type": "Sound", "format": render.AUDIO_TYPES.get(ext, "audio/mpeg"), "duration": dur}
        if is_video:
            from . import video
            body = {"id": f"{m}/media", "type": "Video", "format": video.VIDEO_TYPES.get(ext, "video/mp4"), "duration": dur,
                    "width": media["width"], "height": media["height"]}
        if audio_locked:
            body["service"] = [probe_service(cfg, base, rid, "audio")]
        items = [{"id": f"{canvas}/page/1/audio", "type": "Annotation", "motivation": "painting", "body": body, "target": canvas}]
    vtt = _prune({"id": f"{m}/transcript.vtt", "type": "Text", "format": "text/vtt", "label": lm("Transcript (WebVTT)", "en"), "language": tlang})
    if text_locked:
        vtt["service"] = [probe_service(cfg, base, rid, "transcript")]
    annotations = [{"id": f"{canvas}/captions", "type": "AnnotationPage", "items": [
        {"id": f"{canvas}/captions/1", "type": "Annotation", "motivation": "supplementing", "body": vtt, "target": canvas}]}]
    faces_published = bool(is_video and cfg["video"].get("publish_faces") and level == "public" and d.get("faces_mode") != "off")
    if not text_locked:
        annotations += [{"id": f"{m}/annotations/{k}", "type": "AnnotationPage", "label": lm(v, "en")} for k, v in LAYERS.items()
                        if k in layers and (k not in ("screen", "faces") or is_video) and (k != "faces" or faces_published)]
    cv = {"id": canvas, "type": "Canvas", "label": lm(title, lang), "duration": dur,
          "items": [{"id": f"{canvas}/page/1", "type": "AnnotationPage", "items": items}], "annotations": annotations}
    thumb = None
    if is_video:
        cv.update(width=media["width"], height=media["height"])
        first = next((x for x in d.get("shots") or [] if x.get("frame")), None)
        if first and not audio_locked:
            fw = min(cfg["video"]["frame_width"], media["width"])
            thumb = [{"id": f"{m}/frames/{first['frame'].rsplit('/', 1)[1]}", "type": "Image", "format": "image/jpeg", "width": fw,
                      "height": round(media["height"] * fw / media["width"])}]
            cv["thumbnail"] = thumb
    contexts = ([AUTH2] if (audio_locked and has_audio) or text_locked else []) + ([] if text_locked else [SEARCH2]) + [P3]
    out = {"@context": contexts if len(contexts) > 1 else P3, "id": f"{m}/manifest", "type": "Manifest", "label": meta.get("label") or lm(title, lang),
           "summary": meta.get("summary"), "metadata": _pairs(meta, rec, ns), "rights": meta.get("rights"),
           "requiredStatement": {"label": lm("Attribution", "en"), "value": meta["attribution"]} if meta.get("attribution") else None,
           "provider": _provider(meta, base), "navDate": meta.get("navDate"), "thumbnail": thumb,
           "homepage": [{"id": meta.get("homepage") or f"{base}/#/rec/{rid}", "type": "Text", "label": lm(title, lang), "format": "text/html"}],
           "rendering": None if text_locked else [{"id": f"{m}/transcript.{k}", "type": "Text", "label": lm(v[1], "en"), "format": v[0]} for k, v in DOWNLOADS.items()],
           "seeAlso": [{"id": f"{m}/record.json", "type": "Dataset", "label": lm("Descriptive record (schema.org)", "en"), "format": "application/ld+json", "profile": "https://schema.org/"},
                       {"id": f"{m}/dc.xml", "type": "Dataset", "label": lm("Descriptive record (Dublin Core)", "en"), "format": "application/xml",
                        "profile": "http://www.openarchives.org/OAI/2.0/oai_dc/"}],
           "partOf": [{"id": f"{base}/iiif/collection/{ns}", "type": "Collection"}],
           "service": None if text_locked else [{"id": f"{m}/search", "type": "SearchService2", "service": [{"id": f"{m}/autocomplete", "type": "AutoCompleteService2"}]}],
           "items": [cv]}
    structures = []
    if is_video and d.get("shots"):
        structures.append({"id": f"{m}/range/shots", "type": "Range", "label": lm("Shots", "en"), "items": [
            {"id": f"{m}/range/shot{x['idx'] + 1}", "type": "Range", "label": lm(f"Shot {x['idx'] + 1}", "en"),
             "items": [{"id": f"{canvas}#t={_t(x['t0'])},{_t(x['t1'])}", "type": "Canvas"}]} for x in d["shots"]]})
    if "chapters" in layers and d["sections"]:
        structures.insert(0, {"id": f"{m}/range/contents", "type": "Range", "label": lm("Contents", "en"), "items": [
            {"id": f"{m}/range/{k + 1}", "type": "Range", "label": lm(s.get("title") or f"Part {k + 1}", lang),
             "items": [{"id": f"{canvas}#t={_t(s['t0'])},{_t(s['t1'])}", "type": "Canvas"}]} for k, s in enumerate(d["sections"])]})
    if structures:
        out["structures"] = structures
    return _prune(out)


def annotation_page(db, cfg, rid, base, layer):
    rec = db.one("SELECT language FROM $r", r=R("recording", rid)) or {}
    d = render.player_data(db, rid)
    m = f"{base}/iiif/{rid}"
    canvas, tlang = f"{m}/canvas/1", _lang(rec)
    names, items = {s["key"]: s["name"] for s in d["speakers"]}, []
    segs = d["segments"]
    if layer == "transcript":
        items = [{"id": f"{m}/annotations/transcript/a{i}", "type": "Annotation", "motivation": "supplementing",
                  "body": _prune({"type": "TextualBody", "value": s["text"], "format": "text/plain", "language": tlang}),
                  "target": f"{canvas}#t={_t(s['t0'])},{_t(s['t1'])}"} for i, s in enumerate(segs)]
    elif layer == "speakers":
        turns = []
        for s in segs:
            if turns and turns[-1]["s"] == s["s"]:
                turns[-1]["t1"] = s["t1"]
            else:
                turns.append({"s": s["s"], "t0": s["t0"], "t1": s["t1"]})
        items = [{"id": f"{m}/annotations/speakers/a{i}", "type": "Annotation", "motivation": "tagging",
                  "body": {"type": "TextualBody", "value": names.get(t["s"], "Unknown speaker"), "purpose": "tagging"},
                  "target": f"{canvas}#t={_t(t['t0'])},{_t(t['t1'])}"} for i, t in enumerate(turns)]
    elif layer in ("screen", "faces"):
        media = (db.one("SELECT media FROM $r", r=R("recording", rid)) or {}).get("media") or {}
        W, H = media.get("width") or 1, media.get("height") or 1
        xywh = lambda b: f"{round(b[0] * W)},{round(b[1] * H)},{max(1, round(b[2] * W))},{max(1, round(b[3] * H))}"  # noqa: E731
        if layer == "screen":
            items = [{"id": f"{m}/annotations/screen/a{i}", "type": "Annotation", "motivation": "supplementing",
                      "body": {"type": "TextualBody", "value": x["text"], "format": "text/plain"},
                      "target": f"{canvas}#xywh={xywh(x['box'])}&t={_t(x['t0'])},{_t(x['t1'])}"} for i, x in enumerate(d.get("screen_text") or []) if x.get("box")]
        else:
            for tr in d.get("faces") or []:
                for a, b in tr["spans"]:
                    box = next((bx[1:] for bx in tr["boxes"] if a <= bx[0] < b), None)
                    if box:
                        items.append({"id": f"{m}/annotations/faces/a{len(items)}", "type": "Annotation", "motivation": "tagging",
                                      "body": {"type": "TextualBody", "value": tr["name"], "purpose": "tagging"},
                                      "target": f"{canvas}#xywh={xywh(box)}&t={_t(a)},{_t(b)}"})
    elif layer == "entities":
        n = 0
        for e in d["entities"]:
            for i in e["segs"]:
                if i < len(segs):
                    items.append({"id": f"{m}/annotations/entities/a{n}", "type": "Annotation", "motivation": "tagging",
                                  "body": {"type": "TextualBody", "value": e["name"], "purpose": "tagging"},
                                  "target": f"{canvas}#t={_t(segs[i]['t0'])},{_t(segs[i]['t1'])}"})
                    n += 1
    else:
        raise KeyError(layer)
    return {"@context": P3, "id": f"{m}/annotations/{layer}", "type": "AnnotationPage", "label": lm(LAYERS[layer], "en"), "items": items}


def collection(db, cfg, sid, base, readable=None):
    """A namespace as a Collection: published recordings, plus private ones the requester may read."""
    ns = md.namespace(db, sid)
    meta = ns["meta"]
    items = []
    for r in db.rows("SELECT record::id(id) AS id, title, recorded_at FROM recording WHERE space = $s ORDER BY recorded_at", s=sid):
        eff = md.effective(db, cfg, r["id"])
        if (eff.get("access") or "private") != "private" or (readable is not None and sid in readable):
            items.append(_prune({"id": f"{base}/iiif/{r['id']}/manifest", "type": "Manifest", "label": eff.get("label") or lm(r["title"]),
                                 "navDate": eff.get("navDate")}))
    return _prune({"@context": P3, "id": f"{base}/iiif/collection/{ns['name']}", "type": "Collection", "label": meta.get("label") or lm(ns["name"]),
                   "summary": meta.get("summary"), "metadata": meta.get("metadata"), "rights": meta.get("rights"),
                   "requiredStatement": {"label": lm("Attribution", "en"), "value": meta["attribution"]} if meta.get("attribution") else None,
                   "provider": _provider(meta, base), "partOf": [{"id": f"{base}/iiif/collection", "type": "Collection"}], "items": items})


def root_collection(db, cfg, base, readable=None):
    items = []
    for s in db.rows("SELECT record::id(id) AS id, name FROM space ORDER BY name"):
        c = collection(db, cfg, s["id"], base, readable)
        if c.get("items"):
            items.append({"id": c["id"], "type": "Collection", "label": c["label"]})
    return {"@context": P3, "id": f"{base}/iiif/collection", "type": "Collection", "label": lm(site_label(cfg, base)), "items": items}


# ---------- Content Search 2.0 ----------
WORD = re.compile(r"[\w'’-]+")


def search(db, base, q, rids, page_url, page=0):
    words = [w for w in WORD.findall((q or "").lower()) if len(w) > 1][:6]
    empty = {"@context": SEARCH2, "id": page_url, "type": "AnnotationPage", "items": []}
    if not words or not rids:
        return empty
    try:
        rows = db.rows("SELECT recording, idx, t0, t1, text FROM segment WHERE text @1@ $q AND recording IN $r LIMIT 2000", q=" ".join(words), r=list(rids))
    except Exception:  # noqa: BLE001 - no full-text index on this engine
        rows = [s for s in db.rows("SELECT recording, idx, t0, t1, text FROM segment WHERE recording IN $r", r=list(rids))
                if all(w in s["text"].lower() for w in words)]
    rows.sort(key=lambda s: (s["recording"], s["idx"]))
    total, rows = len(rows), rows[page * PAGE:(page + 1) * PAGE]
    rx = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")[\w'’-]*", re.I)
    items, marks = [], []
    for s in rows:
        aid = f"{base}/iiif/{s['recording']}/annotations/transcript/a{s['idx']}"
        items.append({"id": aid, "type": "Annotation", "motivation": "supplementing", "body": {"type": "TextualBody", "value": s["text"], "format": "text/plain"},
                      "target": f"{base}/iiif/{s['recording']}/canvas/1#t={_t(s['t0'])},{_t(s['t1'])}"})
        for hit in rx.finditer(s["text"]):
            marks.append({"id": f"{page_url}#h{len(marks)}", "type": "Annotation", "motivation": "highlighting",
                          "target": {"type": "SpecificResource", "source": aid, "selector": [{"type": "TextQuoteSelector", "prefix": s["text"][max(0, hit.start() - 30):hit.start()],
                                                                                          "exact": hit.group(0), "suffix": s["text"][hit.end():hit.end() + 30]}]}})
    out = {"@context": SEARCH2, "id": page_url, "type": "AnnotationPage", "items": items}
    if marks:
        out["annotations"] = [{"type": "AnnotationPage", "items": marks}]
    if total > PAGE:
        pages = math.ceil(total / PAGE)
        root = page_url.split("&page=")[0]
        out["partOf"] = {"id": root, "type": "AnnotationCollection", "total": total, "first": {"id": f"{root}&page=0", "type": "AnnotationPage"},
                         "last": {"id": f"{root}&page={pages - 1}", "type": "AnnotationPage"}}
        out["startIndex"] = page * PAGE
        if page + 1 < pages:
            out["next"] = {"id": f"{root}&page={page + 1}", "type": "AnnotationPage"}
        if page:
            out["prev"] = {"id": f"{root}&page={page - 1}", "type": "AnnotationPage"}
    return out


def autocomplete(db, q, rids, page_url):
    pre = (q or "").strip().lower()
    counts = Counter()
    if len(pre) >= 2 and rids:
        for t in db.rows("SELECT surface, n FROM term WHERE recording IN $r", r=list(rids)):
            if t["surface"].lower().startswith(pre):
                counts[t["surface"]] += t["n"]
    return {"@context": SEARCH2, "id": page_url, "type": "TermPage", "items": [{"value": w, "total": n} for w, n in counts.most_common(20)]}


# ---------- Content State 1.0 ----------
def content_state(base, rid, t0=None, t1=None):
    m = f"{base}/iiif/{rid}"
    frag = f"#t={t0:g},{t1:g}" if t0 is not None and t1 is not None else (f"#t={t0:g}" if t0 is not None else "")
    state = {"@context": P3, "id": f"{m}/state{frag.replace('#', '/')}", "type": "Annotation", "motivation": ["contentState"],
             "target": {"id": f"{m}/canvas/1{frag}", "type": "Canvas", "partOf": [{"id": f"{m}/manifest", "type": "Manifest"}]}}
    return state, encode_state(state)


def encode_state(obj):
    """Content State encoding: JSON, then URI-encoded as encodeURIComponent would, then base64url without padding."""
    uri = urllib.parse.quote(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), safe="-_.!~*'()")
    return base64.urlsafe_b64encode(uri.encode()).decode().rstrip("=")


def decode_state(token):
    return json.loads(urllib.parse.unquote(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()))


# ---------- Change Discovery 1.0 ----------
def activity_stream(db, base):
    total = len(db.values("SELECT VALUE id FROM iiif_activity"))
    pages = max(1, math.ceil(total / PAGE))
    root = f"{base}/iiif/discovery/activity"
    return {"@context": DISCOVERY1, "id": root, "type": "OrderedCollection", "totalItems": total,
            "first": {"id": f"{root}/page/0", "type": "OrderedCollectionPage"}, "last": {"id": f"{root}/page/{pages - 1}", "type": "OrderedCollectionPage"}}


def activity_page(db, base, n):
    root = f"{base}/iiif/discovery/activity"
    total = len(db.values("SELECT VALUE id FROM iiif_activity"))
    pages = max(1, math.ceil(total / PAGE))
    if n < 0 or n >= pages:
        raise KeyError(n)
    rows = db.rows(f"SELECT record::id(id) AS id, type, recording, at FROM iiif_activity ORDER BY id LIMIT {PAGE} START {n * PAGE}")
    page = {"@context": DISCOVERY1, "id": f"{root}/page/{n}", "type": "OrderedCollectionPage", "partOf": {"id": root, "type": "OrderedCollection"},
            "startIndex": n * PAGE, "orderedItems": [{"type": r["type"], "object": {"id": f"{base}/iiif/{r['recording']}/manifest", "type": "Manifest"},
                                                      "endTime": r["at"].replace("+00:00", "Z")} for r in rows]}
    if n:
        page["prev"] = {"id": f"{root}/page/{n - 1}", "type": "OrderedCollectionPage"}
    if n + 1 < pages:
        page["next"] = {"id": f"{root}/page/{n + 1}", "type": "OrderedCollectionPage"}
    return page


# ---------- signed links for protected content (what a successful probe hands out) ----------
def sign(cfg, rid, what, ttl=3600):
    exp = int(time.time()) + ttl
    mac = hmac.new(settings.secret_key(cfg), f"{rid}:{what}:{exp}".encode(), "sha256").hexdigest()[:40]
    return f"exp={exp}&sig={mac}"


def signed_ok(cfg, rid, what, exp, sig):
    try:
        if int(exp) < time.time():
            return False
    except (TypeError, ValueError):
        return False
    mac = hmac.new(settings.secret_key(cfg), f"{rid}:{what}:{int(exp)}".encode(), "sha256").hexdigest()[:40]
    return hmac.compare_digest(mac, str(sig or ""))


# ---------- validation ----------
_SCHEMA = []


def validate(doc):
    """Problems against the IIIF Presentation 3 JSON Schema (from IIIF's presentation-validator); None if jsonschema is missing."""
    try:
        import jsonschema
    except ImportError:
        return None
    if not _SCHEMA:
        _SCHEMA.append(jsonschema.Draft7Validator(json.loads((pathlib.Path(__file__).parent / "iiif_schema" / "iiif_3_0.json").read_text())))
    errs = sorted(_SCHEMA[0].iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errs][:20]


# ---------- importing IIIF audio from elsewhere ----------
MAX_JSON, MAX_TEXT, MAX_MEDIA = 10 << 20, 5 << 20, 4 << 30


def _get(url, limit, accept="*/*"):
    if not re.match(r"^https?://", url or ""):
        raise ValueError("use an http(s) address")
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "lens-archive"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is larger than the import limit")
    return data


def fetch_json(url):
    try:
        return json.loads(_get(url, MAX_JSON, 'application/ld+json;profile="http://iiif.io/api/presentation/3/context.json", application/json'))
    except ValueError as e:
        raise ValueError(f"{url} isn't a IIIF JSON document ({e})") from None


def _text(v):
    if isinstance(v, dict):
        return md.first(v)
    if isinstance(v, list):
        return _text(v[0]) if v else None
    return v if isinstance(v, str) else None


def _version(j):
    ctx = " ".join(j.get("@context") if isinstance(j.get("@context"), list) else [str(j.get("@context"))])
    return 3 if "presentation/3" in ctx else 2 if "presentation/2" in ctx else None


def _as_list(v):
    return v if isinstance(v, list) else [v] if v else []


def parse_manifest(j):
    if _version(j) != 3:
        raise ValueError("only IIIF Presentation 3 carries audio; version 2 manifests are images only")
    canvases = []
    for c in j.get("items") or []:
        audio, captions = None, []
        for page in c.get("items") or []:
            for a in page.get("items") or []:
                if "painting" not in _as_list(a.get("motivation")):
                    continue
                for b in _as_list(a.get("body")):
                    for x in _as_list(b.get("items")) if b.get("type") == "Choice" else [b]:
                        if x.get("type") in ("Sound", "Audio", "Video") and not audio:
                            audio = {"id": x.get("id"), "format": x.get("format"), "type": x.get("type"), "duration": x.get("duration")}
        for page in c.get("annotations") or []:
            annos = page.get("items")
            if annos is None and page.get("id"):
                try:
                    annos = fetch_json(page["id"]).get("items") or []
                except ValueError:
                    annos = []
            for a in annos or []:
                for b in _as_list(a.get("body")):
                    if "supplementing" in _as_list(a.get("motivation")) and (b.get("format") == "text/vtt" or str(b.get("id", "")).endswith(".vtt")):
                        captions.append({"id": b.get("id"), "language": b.get("language"), "label": _text(b.get("label"))})
        canvases.append({"canvas": c.get("id"), "label": _text(c.get("label")), "duration": c.get("duration"), "audio": audio, "captions": captions})
    req = j.get("requiredStatement") or {}
    return {"type": "Manifest", "id": j.get("id"), "label": _text(j.get("label")), "labels": j.get("label"), "summary": j.get("summary"),
            "rights": j.get("rights"), "attribution": req.get("value"), "navDate": j.get("navDate"),
            "metadata": [{"label": p.get("label"), "value": p.get("value")} for p in j.get("metadata") or [] if isinstance(p, dict)],
            "homepage": (_as_list(j.get("homepage")) or [{}])[0].get("id"), "items": canvases}


def preview(url):
    j = fetch_json(url)
    if j.get("type") == "Collection" and _version(j) == 3:
        items = [{"id": x.get("id"), "type": x.get("type"), "label": _text(x.get("label"))} for x in j.get("items") or []]
        return {"type": "Collection", "id": j.get("id"), "label": _text(j.get("label")), "total": len(items), "items": items[:200]}
    if j.get("type") not in ("Manifest", "sc:Manifest") and j.get("@type") not in ("sc:Manifest",):
        raise ValueError("that isn't a IIIF Manifest or Collection")
    return parse_manifest(j)


def import_manifest(db, cfg, url, ns, keep_transcripts=True, user=None, log=print):
    p = parse_manifest(fetch_json(url))
    sid, rids = store.ns_id(db, ns), []
    audio_ext = {"audio/mp4": ".m4a", "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac",
                 "audio/webm": ".webm", "video/mp4": ".mp4", "video/webm": ".webm"}
    usable = [c for c in p["items"] if c["audio"] or c["captions"]]
    for n, c in enumerate(usable, 1):
        fp = "iiif-" + hashlib.sha1(f"{url}#{c['canvas']}".encode()).hexdigest()[:24]
        if db.values("SELECT VALUE id FROM recording WHERE fp_key = $k", k=f"{sid}:{fp}"):
            log(f"  {c['canvas']}: already imported")
            continue
        segs = []
        if keep_transcripts and c["captions"]:
            cap = c["captions"][0]
            segs = ingest.read_text_transcript(_get(cap["id"], MAX_TEXT).decode("utf-8", "replace"), "vtt")["segments"]
        local = None
        if c["audio"] and c["audio"].get("id"):
            ext = audio_ext.get((c["audio"].get("format") or "").split(";")[0]) or pathlib.PurePosixPath(urllib.parse.urlsplit(c["audio"]["id"]).path).suffix or ".bin"
            local = pathlib.Path(cfg["data_dir"]) / "iiif-import" / f"{fp}{ext}"
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(_get(c["audio"]["id"], MAX_MEDIA))
        title = c["label"] or p["label"] or "Imported recording"
        if len(usable) > 1 and not c["label"]:
            title = f"{title} ({n})"
        rid = db.next_id("recording")
        dur = int((c.get("duration") or (c["audio"] or {}).get("duration") or 0) * 1000) or None
        db.q("CREATE $r CONTENT $d", r=R("recording", rid), d=store.clean({
            "space": sid, "fingerprint": fp, "fp_key": f"{sid}:{fp}", "title": title[:200], "source": "audio" if local else "transcript",
            "path": str(local) if local else f"iiif:{url}", "iiif_source": {"manifest": url, "canvas": c["canvas"], "audio": (c["audio"] or {}).get("id")},
            "recorded_at": p.get("navDate") or store.now(), "duration_ms": dur, "status": "new", "created_at": store.now()}))
        if segs:
            ingest.write_transcript(db, rid, sid, segs, store.clean({"status": "transcribed", "engine": "import:iiif", "transcribed_at": store.now(),
                                                                    "duration_ms": dur or max(s["t1"] for s in segs)}))
            labels = {s["speaker"] for s in segs if s.get("speaker")}
            if labels:
                spk.assign_labels(db, sid, rid, {l: l for l in labels})
        fields = {"label": p.get("labels") if len(usable) == 1 and not c["label"] else title, "summary": p.get("summary"), "rights": p.get("rights"),
                  "attribution": p.get("attribution"), "navDate": p.get("navDate"), "metadata": p.get("metadata"),
                  "related": [{"id": url, "label": "Imported from this IIIF Manifest"}]}
        for k, v in fields.items():  # keep what's valid, skip what isn't
            try:
                md.save(db, cfg, rid, {k: v}, user=user)
            except md.MetaProblem as e:
                log(f"  {title}: {k} skipped ({e})")
        steps, _ = pipelines.resolve(db, sid)
        steps = [s for s in steps if (s if isinstance(s, str) else s.get("type")) != "transcribe"] if segs else steps
        from . import jobs
        jobs.enqueue(db, rid, steps, by=user or "iiif-import")
        rids.append(rid)
        log(f"  imported {title}")
    return rids


def import_url(db, cfg, url, ns, keep_transcripts=True, user=None, limit=50, log=print):
    j = fetch_json(url)
    if j.get("type") == "Collection":
        rids = []
        for x in (j.get("items") or [])[:limit]:
            if x.get("type") == "Manifest" and x.get("id"):
                try:
                    rids += import_manifest(db, cfg, x["id"], ns, keep_transcripts, user, log)
                except (ValueError, OSError) as e:
                    log(f"  {x['id']}: {e}")
        return rids
    return import_manifest(db, cfg, url, ns, keep_transcripts, user, log)
