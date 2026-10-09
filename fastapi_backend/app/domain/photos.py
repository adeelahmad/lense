"""Search photos by what they show (embeddings.photos, off by default; docs/configuration.md#anytopdf).

anytopdf's CLIP plugin (anytopdf-plugin-clip, kept beside the program by anytopdf.fetch) embeds each image resource
with OpenAI's CLIP ViT-B/32, on the CPU, offline. Its model (about 600 MB) is fetched once into
data_dir/models/clip, which is why this is off by default: a Raspberry Pi can run it, slowly. A query is embedded the
same way, as text, and compared with every photo the person may see, so "a bus on a city street" finds the photo of
one whatever words are on it. These vectors are CLIP's, not the embeddings server's, so they're kept apart from
search by meaning's passages (photo_vector, one per image resource).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile

from . import anytopdf, ingest, netguard, store

log = logging.getLogger(__name__)
R = store.R
PLUGIN = "anytopdf-plugin-clip"
MODEL_FILES = ("visual.onnx", "textual.onnx", "bpe_simple_vocab_16e6.txt.gz")
SECONDS = 120
# how alike a photo must be to a query; CLIP scores text against pictures low: a good match is about 0.28 to 0.32
MIN_SIMILARITY = 0.24  # (a plain blue picture scores 0.21 to 0.24 against most queries)
SPREAD = 0.05  # and no more than this less alike than the best


class Unavailable(RuntimeError):
    """The plugin or its model isn't here."""


def enabled(cfg):
    return bool((cfg.get("embeddings") or {}).get("photos"))


def model_dir(cfg):
    return pathlib.Path(cfg["data_dir"]) / "models" / "clip"


def plugin(cfg):
    """The CLIP plugin beside the anytopdf program Lens fetched (or beside documents.anytopdf), or None."""
    exe = anytopdf.binary(cfg)
    for d in [anytopdf.fetched_path(cfg).parent / "plugins"] + ([pathlib.Path(exe).parent / "plugins"] if exe else []):
        p = d / PLUGIN
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def model_ready(cfg):
    return all((model_dir(cfg) / f).is_file() for f in MODEL_FILES)


def ready(cfg):
    return enabled(cfg) and bool(plugin(cfg)) and model_ready(cfg)


def fetch_model(cfg, say=None):
    """Download the CLIP model with the plugin (it checks each file's checksum)."""
    from . import convert

    exe = plugin(cfg)
    if not exe:
        raise Unavailable("anytopdf's CLIP plugin isn't here")
    d = model_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    if say:
        say("downloading the CLIP model (about 600 MB)")
    r = convert._run([exe, "--fetch-model", str(d)], 3600)
    if r.returncode != 0 or not model_ready(cfg):
        raise RuntimeError(f"the CLIP model wasn't fetched ({convert._last_said(r.stderr or r.stdout) or f'exit {r.returncode}'})")


def _encode(cfg, flag, value):
    """CLIP's vector for a picture (--encode-image) or a text (--encode-text): (vector, model id)."""
    from . import convert

    exe = plugin(cfg)
    if not exe or not model_ready(cfg):
        raise Unavailable("search photos by what they show needs anytopdf's CLIP plugin and its model")
    with tempfile.TemporaryDirectory(prefix="lens-clip-") as tmp:
        env = netguard.nowhere_env({**os.environ, "HOME": tmp, "TMPDIR": tmp})
        env = {k: v for k, v in env.items() if not k.startswith("ANYTOPDF_")}
        env["ANYTOPDF_CLIP_MODEL_DIR"] = str(model_dir(cfg))
        r = convert._run([exe, flag, value], SECONDS, env=env, cwd=tmp)
    try:
        out = json.loads(r.stdout or "")
        vec = [float(x) for x in out["embedding"]]
    except (ValueError, KeyError, TypeError):
        raise ValueError(f"CLIP couldn't read it ({convert._last_said(r.stderr) or f'exit {r.returncode}'})") from None
    return vec, out.get("model") or "clip"


def index_recording(db, cfg, rid, say=print):
    """An image resource's CLIP vector, made once per file. Returns whether one was made."""
    rec = db.one("SELECT source, path, remote, space, collection, fp_key FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") != "image":
        return False
    fp = rec.get("fp_key") or rec.get("path")
    have = db.one("SELECT fp FROM $r", r=R("photo_vector", rid)) or {}
    if fp and have.get("fp") == fp:
        return False
    path = ingest.audio_path(db, cfg, rec)
    if not path or not os.path.exists(path):
        return False
    vec, model = _encode(cfg, "--encode-image", str(path))
    db.q(
        "UPSERT $r CONTENT $d",
        r=R("photo_vector", rid),
        d=store.clean(
            {"recording": rid, "space": rec["space"], "collection": rec.get("collection"), "vector": vec, "model": model, "fp": fp}
        ),
    )
    say("indexed for searching photos by what they show")
    return True


def search(db, cfg, q, spaces, also=(), limit=24):
    """The photos most like `q` in namespaces `spaces` (and recordings `also`), best first: [{recording, similarity}]."""
    q = (q or "").strip()
    if not q:
        return []
    vec, model = _encode(cfg, "--encode-text", q[:300])
    rows = db.rows(
        "SELECT recording, vector::similarity::cosine(vector, $v) AS similarity FROM photo_vector "
        "WHERE model = $m AND (space IN $sp OR recording IN $also) ORDER BY similarity DESC LIMIT $k",
        v=vec,
        m=model,
        sp=sorted(spaces),
        also=sorted(also),
        k=int(limit),
    )
    rows = [r for r in rows if r.get("similarity") is not None]
    least = max([MIN_SIMILARITY] + [r["similarity"] - SPREAD for r in rows[:1]])
    return [{"recording": r["recording"], "similarity": round(float(r["similarity"]), 4)} for r in rows if r["similarity"] >= least]
