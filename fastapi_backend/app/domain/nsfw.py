"""NSFW classification (docs/nsfw.md): a local model scores how likely each picture of a resource is to be not safe
for work (nudity and sexual content), and a resource with a picture at or over the threshold is flagged, so the web
app can blur it or leave it out of the Library.

Off by default. An admin turns it on for every namespace (Settings → NSFW, `nsfw.enabled`), or a namespace's owner
for theirs (PUT /namespaces/{name}/nsfw), which can also have its own threshold and display, or stay off while the rest
is on. The nsfw step then scores a video's sampled frames, a document's pages or an image, on this machine: no picture
leaves it, and no model provider is called.

The model is AdamCodd/vit-base-nsfw-detector (Apache-2.0): a Vision Transformer fine-tuned to tell NSFW from SFW
pictures, as its author's 8-bit ONNX export (88 MB), run by ONNX Runtime on the CPU. components.py fetches both on first
use: the first time a job waits at the nsfw step with classification on somewhere. It was trained to be strict
("sexy" pictures count as NSFW) and is less sure of generated pictures, so a person can mark a resource NSFW or not by
hand, and their call wins over the score.

What a resource keeps (recording.nsfw): `score`, the highest of its pictures'; `flagged`, the person's call (`manual`)
if they made one, else score >= its namespace's threshold (kept in step when the threshold changes: reflag); `hits`,
the pictures that scored highest; `checked` and `of`, how many pictures were scored out of how many it has; `model`
and `at`. Where classification is off, what was kept stays but nothing acts on it.
"""

from __future__ import annotations

import pathlib
import threading

import numpy as np

from . import jobs, keyring, store

R = store.R
MODEL = "vit-base-nsfw-detector-int8.onnx"  # components.FILES fetches it into the data folder's models
MODEL_NAME = "vit-base-nsfw-detector"
MODELS = pathlib.Path("/opt/lens/models")  # models put there by hand, or baked into an image
LABELS = ("sfw", "nsfw")  # the model's id2label
SIZE = 384  # its input, when the model doesn't say
DISPLAYS = ("blur", "hide", "show")
THRESHOLD = (0.5, 0.99)
MAX_FRAMES = (1, 5000)
HITS = 12  # the highest-scoring pictures kept per resource
PICTURED_WHERE = "(source IN ['document', 'image'] OR media.kind = 'video')"  # resources with pictures to classify
_SESSIONS: dict[str, object] = {}
_LOCK = threading.Lock()


class Off(ValueError):
    """Classification is off in this namespace."""


# ---------- who it's on for ----------
def own(db, sid):
    """What a namespace chose for itself: {enabled, threshold, display}, each only when it chose."""
    return dict((db.one("SELECT nsfw FROM $s", s=R("space", int(sid))) or {}).get("nsfw") or {})


def _merge(cfg, mine):
    g = cfg.get("nsfw") or {}
    on = mine.get("enabled")
    return {
        "enabled": bool(g.get("enabled")) if on is None else bool(on),
        "threshold": float(mine.get("threshold") or g.get("threshold") or 0.8),
        "display": mine.get("display") or g.get("display") or "blur",
        "max_frames": int(g.get("max_frames") or 300),
    }


def policy(db, cfg, sid):
    """What applies in a namespace: its own choices, else the archive's (`nsfw` settings)."""
    return _merge(cfg, own(db, sid))


def policies(db, cfg, sids):
    """{namespace id: policy} for many namespaces, in one query."""
    sids = sorted({int(s) for s in sids if s is not None})
    if not sids:
        return {}
    rows = db.rows("SELECT record::id(id) AS id, nsfw FROM space WHERE id IN $ids", ids=[R("space", s) for s in sids])
    mine = {r["id"]: dict(r.get("nsfw") or {}) for r in rows}
    return {s: _merge(cfg, mine.get(s) or {}) for s in sids}


def any_namespace(db):
    """Whether a namespace turned it on for itself."""
    try:
        return bool(db.values("SELECT VALUE id FROM space WHERE nsfw.enabled = true LIMIT 1"))
    except Exception:  # noqa: BLE001 - asked again on the next pass
        return False


def wanted(db, cfg):
    """Whether it's on anywhere: what has the model fetched (components.py)."""
    return bool((cfg.get("nsfw") or {}).get("enabled")) or (db is not None and any_namespace(db))


def check_threshold(value, name="threshold"):
    lo, hi = THRESHOLD
    if not (isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"{name} is a number from {lo} to {hi}")
    return round(float(value), 3)


def check_display(value, name="display"):
    if value not in DISPLAYS:
        raise ValueError(f"{name} is one of {', '.join(DISPLAYS)}")
    return value


def save_own(db, sid, changes):
    """Change what a namespace chose for itself; a key set to None goes back to the archive's setting."""
    mine = own(db, sid)
    for k, v in (changes or {}).items():
        if k not in ("enabled", "threshold", "display"):
            raise ValueError(f"there's no NSFW setting called {k}")
        if v is None:
            mine.pop(k, None)
        elif k == "enabled":
            if not isinstance(v, bool):
                raise ValueError("enabled is true or false")
            mine[k] = v
        else:
            mine[k] = check_threshold(v) if k == "threshold" else check_display(v)
    db.q("UPDATE $s SET nsfw = $n", s=R("space", int(sid)), n=mine or None)
    return mine


def reflag(db, cfg, sids=None, rid=None):
    """Keep `flagged` in step with each namespace's threshold (after it changes, or a resource moves; `rid`: only that
    one): a person's own call stays as it is."""
    spaces = sids if sids is not None else db.values("SELECT VALUE record::id(id) FROM space")
    one = " AND id = $r" if rid is not None else ""
    for sid, p in policies(db, cfg, spaces).items():
        db.q(
            "UPDATE recording SET nsfw.flagged = nsfw.score >= $t WHERE space = $s AND nsfw.score != NONE AND nsfw.manual = NONE" + one,
            s=sid,
            t=p["threshold"],
            r=R("recording", rid) if rid is not None else None,
        )


def brief(n, p):
    """A resource's classification as the API shows it, under its namespace's policy; None where it's off or the
    resource wasn't classified."""
    if not n or not p or not p.get("enabled") or (n.get("score") is None and n.get("manual") is None):
        return None
    flagged = bool(n.get("flagged"))
    return store.clean(
        {
            "flagged": flagged,
            "score": n.get("score"),
            "manual": n.get("manual"),
            "blur": flagged and p["display"] != "show",
            "display": p["display"],
            "threshold": p["threshold"],
            "checked": n.get("checked"),
            "of": n.get("of"),
            "at": n.get("at"),
        }
    )


def briefs(db, cfg, rows):
    """{recording id: brief} for rows with `id`, `space` and `nsfw` (only those that have one)."""
    ps = policies(db, cfg, {r["space"] for r in rows if r.get("nsfw")}) if cfg is not None else {}
    out = {}
    for r in rows:
        b = brief(r.get("nsfw"), ps.get(r["space"]))
        if b:
            out[r["id"]] = b
    return out


def blurred(db, cfg, rids):
    """Which of these resources the web app blurs: flagged, where classification is on and doesn't show them as they are."""
    rids = sorted({int(r) for r in rids if r is not None})
    if not rids:
        return set()
    rows = db.rows(
        "SELECT record::id(id) AS id, space, nsfw FROM recording WHERE id IN $r AND nsfw.flagged = true",
        r=[R("recording", x) for x in rids],
    )
    return {rid for rid, b in briefs(db, cfg, rows).items() if b.get("blur")}


def flagged_spaces(db, cfg, spaces):
    """(namespaces where it's on, those that hide flagged resources) among `spaces`."""
    ps = policies(db, cfg, spaces)
    on = sorted(s for s, p in ps.items() if p["enabled"])
    return on, sorted(s for s in on if ps[s]["display"] == "hide")


def mark(db, cfg, rid, flagged, user=None):
    """A person's own call: NSFW (True), not (False), or None to go back to the score."""
    rec = db.one("SELECT space, nsfw FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    p = policy(db, cfg, rec["space"])
    if not p["enabled"]:
        raise Off("NSFW classification is off in this namespace")
    n = dict(rec.get("nsfw") or {})
    if flagged is None:
        for k in ("manual", "manual_by", "manual_at"):
            n.pop(k, None)
        if n.get("score") is None:
            n = {}
        else:
            n["flagged"] = n["score"] >= p["threshold"]
    else:
        n.update(manual=bool(flagged), flagged=bool(flagged), manual_by=user, manual_at=store.now())
    db.q("UPDATE $r SET nsfw = $n", r=R("recording", rid), n=store.clean(n) or None)
    return brief(n, p)


def queue_unclassified(db, sid, by=None):
    """Queue the nsfw step for a namespace's videos, documents and images that weren't classified yet."""
    rows = db.values(f"SELECT VALUE record::id(id) FROM recording WHERE space = $s AND nsfw.score = NONE AND {PICTURED_WHERE}", s=int(sid))
    return [jobs.add_steps(db, rid, ["nsfw"], by=by) for rid in rows]


def _count(db, where, **params):
    got = db.values(f"RETURN array::len((SELECT VALUE id FROM recording WHERE {where}))", **params)
    return int(got[0]) if got else 0


def status(db, cfg, sid):
    """A namespace's classification: what applies, what it chose itself, the archive's settings, and how far it got."""
    g, mine = cfg.get("nsfw") or {}, own(db, sid)
    p = _merge(cfg, mine)
    here = "space = $s AND " + PICTURED_WHERE
    return {
        "enabled": p["enabled"],
        "threshold": p["threshold"],
        "display": p["display"],
        "own": mine,
        "archive": {k: g.get(k) for k in ("enabled", "threshold", "display")},
        "classified": _count(db, here + " AND nsfw.score != NONE", s=int(sid)),
        "flagged": _count(db, here + " AND nsfw.flagged = true", s=int(sid)),
        "unclassified": _count(db, here + " AND nsfw.score = NONE", s=int(sid)),
    }


# ---------- the model ----------
def model_path(cfg):
    """The model file: one put in MODELS by hand, else the one components.py fetched; None without one."""
    from . import components

    for d in (MODELS, components.models_dir(cfg)):
        p = d / MODEL
        if p.is_file():
            return str(p)
    return None


def _session(path):
    import onnxruntime as ort

    with _LOCK:  # one per process: loading 88 MB for every resource would take longer than scoring it
        if path not in _SESSIONS:
            _SESSIONS[path] = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        return _SESSIONS[path]


def _pixels(img, size):
    """The picture as the model was trained on it: RGB, resized to its input, scaled to [-1, 1], channels first."""
    from PIL import Image

    x = np.asarray(img.convert("RGB").resize((size[1], size[0]), Image.BILINEAR), dtype=np.float32) / 255.0
    return ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]


class VitOnnx:
    name = MODEL_NAME

    def __init__(self, cfg):
        path = model_path(cfg)
        if not path:
            raise RuntimeError("the NSFW model isn't here yet (it's fetched the first time a job needs it)")
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            raise RuntimeError("ONNX Runtime isn't installed (it's fetched on first use, or pip install \"lens[objects]\")") from None
        self.session = _session(path)
        inp = self.session.get_inputs()[0]
        self.input = inp.name
        h, w = (list(inp.shape) + [None, None, None, None])[2:4]
        self.size = (h if isinstance(h, int) else SIZE, w if isinstance(w, int) else SIZE)

    def score(self, path):
        """How likely the picture at `path` is to be NSFW, from 0 to 1."""
        from PIL import Image

        with Image.open(path) as img:
            x = _pixels(img, self.size)
        logits = np.asarray(self.session.run(None, {self.input: x})[0][0], dtype=np.float64)
        p = np.exp(logits - logits.max())
        return float(p[LABELS.index("nsfw")] / p.sum())


def engine(cfg):
    """(the classifier, None), or (None, why there's none)."""
    try:
        return VitOnnx(cfg), None
    except RuntimeError as e:
        return None, str(e)


def spread(items, n):
    """At most n of the items, evenly spread from the first to the last."""
    if n <= 0 or len(items) <= n:
        return list(items)
    if n == 1:
        return [items[0]]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


# ---------- the step ----------
def step(db, cfg, rid, say):
    """The nsfw step: score a video's sampled frames, or a document's or an image's pages, and keep the highest."""
    from . import documents, video

    rec = db.one("SELECT space, source, media, samples, sample_ms, nsfw FROM $r", r=R("recording", rid)) or {}
    p = policy(db, cfg, rec.get("space"))
    if not p["enabled"]:
        raise jobs.Skip("NSFW classification is off in this namespace (Settings → NSFW, or the namespace's page)")
    paged = rec.get("source") in ("document", "image")
    if not paged and (rec.get("media") or {}).get("kind") != "video":
        raise jobs.Skip("it has no pictures (it isn't a video, a document or an image)")
    if paged:
        frames = [[pg["idx"], pg["image"]] for pg in documents.pages(db, rid) if pg.get("image")]
        if not frames:
            raise jobs.Skip("its pages weren't drawn")
    else:
        frames = rec.get("samples") or []
        if not frames:
            raise jobs.Skip("it has no sampled frames yet (the shots step samples them)")
    found, why = engine(cfg)
    if not found:
        raise jobs.Skip(why)
    look, d, scored = spread(frames, p["max_frames"]), video.frames_dir(cfg, rid), []
    for t, name in look:
        with keyring.plain_picture(db, cfg, d / name) as pic:
            scored.append((t, name, found.score(pic)))
    top = sorted(scored, key=lambda x: -x[2])[:HITS]
    best = top[0][2]
    manual = (rec.get("nsfw") or {}).get("manual")
    flagged = manual if manual is not None else best >= p["threshold"]
    keep = {k: v for k, v in (rec.get("nsfw") or {}).items() if k in ("manual", "manual_by", "manual_at")}
    n = store.clean(
        {
            **keep,
            "score": round(best, 4),
            "flagged": bool(flagged),
            "hits": [{"t": t, "frame": name, "score": round(s, 4)} for t, name, s in top],
            "checked": len(look),
            "of": len(frames),
            "paged": paged or None,
            "model": found.name,
            "at": store.now(),
        }
    )
    db.q("UPDATE $r SET nsfw = $n", r=R("recording", rid), n=n)
    what = "page(s)" if paged else "frame(s)"
    of = f" of {len(frames)}" if len(look) < len(frames) else ""
    verdict = "flagged NSFW" if flagged else "not NSFW"
    say(f"{verdict}: highest score {best:.2f} (threshold {p['threshold']}) over {len(look)}{of} {what} ({found.name})")


def hits(n, rid):
    """A resource's highest-scoring pictures, with links to them (signed by the route)."""
    return [
        {"t": h["t"], "score": h["score"], "frame": f"{store.API}/recordings/{rid}/frames/{h['frame']}"}
        for h in (n or {}).get("hits") or []
    ]
