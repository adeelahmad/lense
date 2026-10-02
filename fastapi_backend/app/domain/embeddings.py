"""Search by meaning (docs/processing.md#embed, docs/configuration.md#search-by-meaning): a small sentence-embedding
model turns each line of a transcript, each block of a document's text and each description of a page or a shot into
a vector, and a search into another; the lines whose vectors point the same way are about the same thing, whatever
words they use.

Off unless an admin switches it on (search.semantic). The default model is all-MiniLM-L6-v2 (English, 384 numbers a
line, Apache-2.0) run on ONNX Runtime with the tokenizers library, both Apache-2.0: it's fetched from Hugging Face
on first use into data_dir/models and checked against its hash. search.semantic_model (startup only) names a folder
with another model's `model.onnx` and `tokenizer.json`, such as the multilingual MiniLM.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import threading
import urllib.request

import numpy as np

from . import jobs, store

R = store.R
log = logging.getLogger("lens")
NAME = "all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
SOURCE = f"https://huggingface.co/sentence-transformers/{NAME}/resolve/{REVISION}"
FILES = {
    "model.onnx": ("onnx/model.onnx", "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452"),
    "tokenizer.json": ("tokenizer.json", "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"),
}
MAX_TOKENS = 256  # what the model was trained on; longer passages are cut there
BATCH = 32
MIN_CHARS = 12  # shorter lines ("Yes.", "Thanks.") mean too little to be found by
_MODELS, _LOCK = {}, threading.Lock()


def enabled(cfg):
    return bool(cfg["search"].get("semantic"))


def model_dir(cfg):
    """(the folder the model is in, whether it's the one Lens fetches itself)."""
    named = cfg["search"].get("semantic_model")
    if named:
        return pathlib.Path(named).expanduser(), False
    return pathlib.Path(cfg["data_dir"]) / "models" / NAME, True


def model_name(cfg):
    return model_dir(cfg)[0].name


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(folder, say=None, opener=urllib.request.urlopen):
    """Download the default model into `folder`, each file checked against its hash before it's put in place."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, (remote, sha) in FILES.items():
        dest = folder / name
        if dest.is_file():
            continue
        if say:
            say(f"fetching {NAME}/{name}")
        tmp = folder / f".{name}.{os.getpid()}.part"
        try:
            with opener(f"{SOURCE}/{remote}", timeout=120) as r, open(tmp, "wb") as f:
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    f.write(chunk)
            if _sha256(tmp) != sha:
                raise RuntimeError(f"{name} of {NAME} isn't the file expected (its hash differs); nothing was kept")
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)


class Embedder:
    """A sentence-embedding model: texts in, one unit vector each out."""

    def __init__(self, folder):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError:
            raise RuntimeError('ONNX Runtime and tokenizers aren\'t installed (pip install "lens[semantic]")') from None
        self.name = folder.name
        self.tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        self.session = ort.InferenceSession(str(folder / "model.onnx"), sess_options=opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self._run = threading.Lock()

    def embed(self, texts):
        """An array of one row per text, each of length 1 (so a dot product is the cosine)."""
        out = []
        for i in range(0, len(texts), BATCH):
            enc = self.tokenizer.encode_batch([t or "" for t in texts[i : i + BATCH]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            with self._run:
                hidden = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            m = mask[:, :, None].astype(np.float32)
            pooled = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)  # the mean of the tokens that aren't padding
            out.append(pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12))
        return np.concatenate(out) if out else np.zeros((0, 0), dtype=np.float32)


def why_not(cfg):
    """Why there's no search by meaning, without loading anything: None when there may be."""
    if not enabled(cfg):
        return "search by meaning is off (search.semantic)"
    folder, ours = model_dir(cfg)
    if not ours and not all((folder / n).is_file() for n in FILES):
        return f"no model at {folder} (it needs model.onnx and tokenizer.json)"
    return None


def embedder(cfg, say=None):
    """The model, loaded once per process (fetching the default one the first time). RuntimeError says why not."""
    why = why_not(cfg)
    if why:
        raise RuntimeError(why)
    folder, ours = model_dir(cfg)
    with _LOCK:
        if str(folder) not in _MODELS:
            if ours and not all((folder / n).is_file() for n in FILES):
                try:
                    fetch(folder, say)
                except OSError as e:
                    raise RuntimeError(f"couldn't fetch {NAME} from huggingface.co ({e}); put its files in {folder}") from None
            _MODELS[str(folder)] = Embedder(folder)
        return _MODELS[str(folder)]


def status(cfg):
    """What Settings shows: {enabled, model, ready, reason}."""
    folder, ours = model_dir(cfg)
    there = all((folder / n).is_file() for n in FILES)
    reason = why_not(cfg)
    if not reason:
        try:
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
        except ImportError:
            reason = 'ONNX Runtime and tokenizers aren\'t installed (pip install "lens[semantic]")'
    if not reason and not there:
        reason = f"{NAME} is fetched the first time something is embedded (about 90 MB)"
    return {"enabled": enabled(cfg), "model": folder.name, "path": str(folder), "ready": there and not why_not(cfg), "reason": reason}


def _h(text):
    return hashlib.sha1((text or "").encode()).hexdigest()[:16]


def passages(db, rid):
    """What's embedded of a resource: its lines (a transcript's, or the blocks of a document's text) and the
    descriptions of its pages or shots. [{key, kind, ref, text}]"""
    out = [
        {"key": f"s{s['id']}", "kind": "segment", "ref": s["id"], "text": s["text"]}
        for s in db.rows("SELECT record::id(id) AS id, idx, text FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    ]
    out += [
        {"key": f"d{rid}-{d['idx']}", "kind": "description", "ref": d["idx"], "text": d["text"]}
        for d in db.rows("SELECT idx, text FROM description WHERE recording = $r ORDER BY idx", r=rid)
    ]
    return [p for p in out if len((p["text"] or "").strip()) >= MIN_CHARS]


def step_embed(db, cfg, rid, say):
    """The embed step: a vector for each passage that has none yet, or whose text or model changed."""
    why = why_not(cfg)
    if why:
        raise jobs.Skip(why)
    rec = db.one("SELECT space FROM $r", r=R("recording", rid)) or {}
    want = passages(db, rid)
    if not want:
        db.q("DELETE embedding WHERE recording = $r", r=rid)
        raise jobs.Skip("there's no text to embed")
    try:
        model = embedder(cfg, say)
    except RuntimeError as e:
        raise jobs.Skip(str(e)) from None
    have = {e["key"]: e for e in db.rows("SELECT record::id(id) AS key, h, model FROM embedding WHERE recording = $r", r=rid)}
    todo = [p for p in want if (have.get(p["key"]) or {}).get("h") != _h(p["text"]) or have[p["key"]].get("model") != model.name]
    gone = sorted(set(have) - {p["key"] for p in want})
    for i in range(0, len(todo), 256):
        part = todo[i : i + 256]
        vecs = model.embed([p["text"] for p in part])
        rows = [
            {
                "id": R("embedding", p["key"]),
                "recording": rid,
                "space": rec["space"],
                "kind": p["kind"],
                "ref": p["ref"],
                "h": _h(p["text"]),
                "model": model.name,
                "vec": [round(float(x), 6) for x in v],
            }
            for p, v in zip(part, vecs)
        ]
        # two statements, not one transaction: SurrealDB 3.2 drops records deleted and created again in the same one
        stale = [r["id"] for r in rows if str(r["id"].id) in have]
        if stale:
            db.q("DELETE embedding WHERE id IN $ids", ids=stale)
        db.q("INSERT INTO embedding $rows", rows=rows)
    if gone:
        db.q("DELETE embedding WHERE id IN $ids", ids=[R("embedding", k) for k in gone])
    say(f"{len(todo)} passage(s) embedded ({model.name}); {len(want) - len(todo)} already were")


def nearest(db, cfg, text, where="", params=None, limit=200, kinds=("segment", "description")):
    """The passages closest in meaning to `text`, the closest first: [{kind, ref, recording, space, h, sim}], among
    those `where` allows (a SurrealQL condition starting with " AND ", over recording and space). [] when search by
    meaning is off or its model can't be loaded."""
    try:
        model = embedder(cfg)
    except RuntimeError as e:
        log.info("no search by meaning: %s", e)
        return []
    q = [float(x) for x in model.embed([text])[0]]
    return db.rows(
        "SELECT kind, ref, recording, space, h, vector::similarity::cosine(vec, $qv) AS sim FROM embedding "
        f"WHERE model = $model AND kind IN $kinds{where} ORDER BY sim DESC LIMIT {int(limit)}",
        **{**(params or {}), "qv": q, "model": model.name, "kinds": list(kinds)},
    )


def fresh(text, h):
    """Whether a vector still stands for this text (it doesn't after a correction, until the line is embedded again)."""
    return _h(text) == h
