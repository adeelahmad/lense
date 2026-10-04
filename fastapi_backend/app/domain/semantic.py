"""Search by meaning: passages of what was said, what pages say and what pictures show, embedded by an
OpenAI-compatible embeddings server (Ollama, llama.cpp, vLLM, LM Studio, OpenAI) and kept in SurrealDB's HNSW index.

A passage is a run of consecutive transcript lines (or lines of one page) up to `embeddings.passage_chars` long, or one
description of a shot or a page. Each is a `passage` row with its vector, keyed by where it starts, so indexing a
recording again only embeds the passages whose text changed. The index is made for the model's dimension the first
time a vector is stored; when the model (or what it's told passages are) changes, every vector is dropped, since
vectors from different models can't be compared, and recordings are indexed again by their pipelines or by the
"Index for search by meaning" routine.

search.py asks `nearest` for the passages closest to a query and fuses them with the BM25 hits by reciprocal rank.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict

from . import activity, store

R = store.R
log = logging.getLogger("lens")
STATE = R("embedding_state", "current")
FAILING = R("embedding_state", "failing")  # the server failed indexing: jobs skip until `until` (epoch seconds)
FAIL_SECONDS = 600
INDEX = "passage_vec"
EMBED_CHARS = 2000  # the most of a passage sent to the model (small models read 512 tokens)
SNIPPET_CHARS = 240
FIELDS = "record::id(id) AS id, recording, space, kind, idx0, idx1, page, t0, t1, text, frame, paged"
# What some embedding models want to be told a text is, as (query, document) prefixes; matched in their names.
# Models not listed get none. embeddings.query_prefix and .document_prefix override these.
PREFIXES = (
    ("nomic-embed", ("search_query: ", "search_document: ")),
    ("mxbai-embed", ("Represent this sentence for searching relevant passages: ", "")),
    ("snowflake-arctic-embed", ("Represent this sentence for searching relevant passages: ", "")),
    ("bge-small-en", ("Represent this sentence for searching relevant passages: ", "")),
    ("bge-base-en", ("Represent this sentence for searching relevant passages: ", "")),
    ("bge-large-en", ("Represent this sentence for searching relevant passages: ", "")),
    ("e5-", ("query: ", "passage: ")),
)
# How similar (cosine) a passage must be to a query to count as about it: models spread their scores differently
# (nomic-embed-text scores unrelated text around 0.4 to 0.5). embeddings.min_similarity overrides these.
FLOORS = (
    ("nomic-embed", 0.52),
    ("mxbai-embed", 0.5),
    ("snowflake-arctic-embed", 0.3),
    ("bge-m3", 0.45),
    ("bge-", 0.6),
    ("e5-", 0.8),
    ("minilm", 0.3),
    ("text-embedding-3", 0.3),
    ("text-embedding-ada", 0.75),
)
DEFAULT_FLOOR = 0.45
# and how far below the closest passage the others may be: most of what's further than that is only loosely related
SPREAD = 0.12


class EmbedError(RuntimeError):
    pass


# ---------- the model ----------
def _section(cfg):
    return {**store.DEFAULTS["embeddings"], **(cfg.get("embeddings") or {})}


def endpoint(cfg):
    """(base URL, API key, model) of the embeddings server: its own, else the LLM provider's."""
    e, l = _section(cfg), cfg["llm"]
    own = bool(e.get("base_url"))
    base = e.get("base_url") if own else l.get("base_url")
    key = e.get("api_key") or (os.environ.get(e["api_key_env"]) if e.get("api_key_env") else None)
    if not key and not own:
        key = l.get("api_key") or (os.environ.get(l["api_key_env"]) if l.get("api_key_env") else None)
    return (base or "").rstrip("/") or None, key, (e.get("model") or "").strip() or None


def configured(cfg):
    """Whether search by meaning is on and has a server and a model to ask."""
    base, _, model = endpoint(cfg)
    return bool(_section(cfg).get("enabled") and base and model)


def _known(table, model, default):
    m = (model or "").lower()
    return next((v for k, v in table if k in m), default)


def prefixes(cfg):
    """(query prefix, document prefix) for the configured model."""
    e = _section(cfg)
    q, d = _known(PREFIXES, endpoint(cfg)[2], ("", ""))
    return (q if e.get("query_prefix") is None else e["query_prefix"]), (d if e.get("document_prefix") is None else e["document_prefix"])


def floor(cfg):
    """The least similarity a passage needs to be a hit."""
    v = _section(cfg).get("min_similarity")
    return float(v) if v is not None else _known(FLOORS, endpoint(cfg)[2], DEFAULT_FLOOR)


def ident(cfg):
    """What makes vectors comparable: the model, and what passages are prefixed with."""
    return f"{endpoint(cfg)[2]}|{prefixes(cfg)[1]}"


def embed(cfg, texts, timeout=None):
    """Vectors for `texts`, in order (POST /embeddings). EmbedError when the server can't give them."""
    base, key, model = endpoint(cfg)
    if not (base and model):
        raise EmbedError("no embeddings server or model is configured (Settings → Search)")
    if not texts:
        return []
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = json.dumps({"model": model, "input": [t[:EMBED_CHARS] for t in texts]}).encode()
    req = urllib.request.Request(base + "/embeddings", data=body, headers=headers, method="POST")
    with activity.call("embeddings", cfg, model, detail={"texts": len(texts)}, price=activity.token_cost(cfg)) as ledger:
        try:
            with urllib.request.urlopen(req, timeout=timeout or _section(cfg).get("timeout") or 60) as r:
                j = json.load(r)
        except urllib.error.HTTPError as e:
            raise EmbedError(f"{e.code} from the embeddings server for {model}: {e.read().decode('utf-8', 'replace')[:300]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise EmbedError(f"can't reach the embeddings server at {base}: {e}") from None
        except ValueError:
            raise EmbedError("the embeddings server's reply wasn't JSON") from None
        u = j.get("usage") if isinstance(j, dict) and isinstance(j.get("usage"), dict) else {}
        ledger.usage(u.get("prompt_tokens", u.get("total_tokens")))
    try:
        data = sorted(j["data"], key=lambda d: d.get("index", 0))
        vecs = [[float(x) for x in d["embedding"]] for d in data]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise EmbedError("unexpected reply from the embeddings server") from None
    if len(vecs) != len(texts) or not vecs or len({len(v) for v in vecs}) != 1 or not vecs[0]:
        raise EmbedError("the embeddings server sent back the wrong number of vectors")
    return vecs


_QUERIES: OrderedDict = OrderedDict()  # (ident, base, text) -> vector, the latest 256 queries
_DOWN: dict = {}  # base URL -> (until, error): a server that just failed isn't asked again for a little while
_QL = threading.Lock()
DOWN_SECONDS = 30


def query_vector(cfg, text):
    """The vector of a search, remembered for the next page of results. EmbedError when the server can't give it."""
    base = endpoint(cfg)[0]
    k = (ident(cfg), base, text)
    with _QL:
        if k in _QUERIES:
            _QUERIES.move_to_end(k)
            return _QUERIES[k]
        down = _DOWN.get(base)
        if down and down[0] > time.monotonic():
            raise EmbedError(down[1])
    try:
        v = embed(cfg, [prefixes(cfg)[0] + text], timeout=min(15, _section(cfg).get("timeout") or 15))[0]
    except EmbedError as e:
        with _QL:
            _DOWN[base] = (time.monotonic() + DOWN_SECONDS, str(e))
        raise
    with _QL:
        _DOWN.pop(base, None)
        _QUERIES[k] = v
        while len(_QUERIES) > 256:
            _QUERIES.popitem(last=False)
    return v


# ---------- the index ----------
def state(db):
    """{ident, model, dimension, at} of the vectors stored, or None before the first."""
    return db.one("SELECT ident, model, dimension, at FROM $s", s=STATE)


_defined: set = set()


def _define(db, dimension):
    """Define the HNSW index once per process. The embedded engine searches by brute force (its KNN
    drops rows under filters) and inserting into HNSW there is slow, so it gets no index."""
    key = (id(db), int(dimension))
    if db.embedded or key in _defined:
        return
    db.q(f"DEFINE INDEX IF NOT EXISTS {INDEX} ON passage FIELDS embedding HNSW DIMENSION {int(dimension)} DIST COSINE TYPE F32")
    _defined.add(key)


def ensure_index(db, cfg, dimension):
    """Make the vector index for this model's vectors; when they're another model's, drop those first."""
    st = state(db) or {}
    if st.get("ident") == ident(cfg) and st.get("dimension") == dimension:
        _define(db, dimension)
        return
    if st:
        log.info("search by meaning: the model changed (%s → %s); dropping the old vectors", st.get("ident"), ident(cfg))
    db.q(f"REMOVE INDEX IF EXISTS {INDEX} ON passage")
    db.q("DELETE passage")
    db.q("UPDATE recording SET embedded = NONE WHERE embedded != NONE")
    _defined.discard((id(db), int(dimension)))
    _define(db, dimension)
    db.q(
        "UPSERT $s CONTENT $d",
        s=STATE,
        d={"ident": ident(cfg), "model": endpoint(cfg)[2], "dimension": int(dimension), "at": store.now()},
    )


def available(db, cfg):
    """Whether searches can be by meaning: it's configured and the stored vectors are the configured model's."""
    if not configured(cfg):
        return False
    st = state(db)
    return bool(st and st.get("ident") == ident(cfg))


def status(db, cfg):
    """What the admin sees: whether it's on, the model, and how much of the archive is indexed."""
    st = state(db) or {}
    base, _, model = endpoint(cfg)
    current = bool(st) and st.get("ident") == ident(cfg)
    n = lambda q, **p: int((db.one(q, **p) or {}).get("n") or 0)  # noqa: E731
    return {
        "enabled": bool(_section(cfg).get("enabled")),
        "configured": configured(cfg),
        "base_url": base,
        "model": model,
        "indexed_model": st.get("model"),
        "current": current,
        "dimension": st.get("dimension") if current else None,
        "passages": n("SELECT count() AS n FROM passage GROUP ALL") if current else 0,
        "recordings": n("SELECT count() AS n FROM recording WHERE status NOT IN ['new', 'error'] GROUP ALL"),
        "indexed": n(
            "SELECT count() AS n FROM recording WHERE status NOT IN ['new', 'error'] AND embedded.ident = $i GROUP ALL", i=ident(cfg)
        )
        if current
        else 0,
        "min_similarity": floor(cfg),
    }


# ---------- passages ----------
def _hash(text, idn):
    return hashlib.sha1(f"{idn}\n{text}".encode()).hexdigest()


def _meta(p):
    """What a passage says about its text (where, who, how): when only this changed, its vector is kept."""
    keep = {k: v for k, v in p.items() if k not in ("key", "text", "hash", "meta", "embedding")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


def build(db, cfg, rid):
    """The passages of a recording: runs of its lines (each within one page), and what its shots or pages show."""
    rec = db.one("SELECT space FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    size, idn, out = int(_section(cfg).get("passage_chars") or 800), ident(cfg), []
    segs = db.rows("SELECT idx, t0, t1, text, page, speaker, emotion FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    run: list = []

    def flush():
        if not run:
            return
        text = " ".join(" ".join(s["text"].split()) for s in run)
        out.append(
            {
                "key": f"{rid}-s{run[0]['idx']}",
                "kind": "page" if run[0].get("page") is not None else "said",
                "idx0": run[0]["idx"],
                "idx1": run[-1]["idx"],
                "page": run[0].get("page"),
                "t0": run[0].get("t0"),
                "t1": run[-1].get("t1"),
                "speakers": sorted({s["speaker"] for s in run if s.get("speaker") is not None}),
                "emotions": sorted({s["emotion"] for s in run if s.get("emotion")}),
                "text": text,
            }
        )
        run.clear()

    for s in segs:
        if not (s.get("text") or "").strip():
            continue
        if run and (s.get("page") != run[0].get("page") or sum(len(x["text"]) + 1 for x in run) + len(s["text"]) > size):
            flush()
        run.append(s)
    flush()
    for d in db.rows("SELECT idx, t0, t1, text, frame, paged FROM description WHERE recording = $r ORDER BY idx", r=rid):
        text = " ".join((d.get("text") or "").split())
        if text:
            out.append(
                {
                    "key": f"{rid}-d{d['idx']}",
                    "kind": "described",
                    "t0": d.get("t0"),
                    "t1": d.get("t1"),
                    "frame": d.get("frame"),
                    "paged": d.get("paged"),
                    "text": text,
                }
            )
    for p in out:
        p.update(recording=rid, space=rec["space"], hash=_hash(p["text"], idn))
        p["meta"] = _meta(p)
    return out


def failing(db, cfg):
    """Why indexing with the configured model failed a moment ago, while jobs should leave the server be; else None."""
    f = db.one("SELECT ident, until, error FROM $f", f=FAILING)
    return f["error"] if f and f.get("ident") == ident(cfg) and float(f.get("until") or 0) > time.time() else None


def recovered(db):
    """Let jobs ask the server again (it answered a test, or an admin asked to index)."""
    db.q("DELETE $f", f=FAILING)


def _embed_passages(db, cfg, texts):
    """embed(), remembering a failure so other jobs skip for FAIL_SECONDS rather than each waiting on a server that's
    down or lacks the model."""
    why = failing(db, cfg)
    if why:
        raise EmbedError(why)
    try:
        return embed(cfg, texts)
    except EmbedError as e:
        db.q("UPSERT $f CONTENT $d", f=FAILING, d={"ident": ident(cfg), "until": time.time() + FAIL_SECONDS, "error": str(e)})
        raise


def index_recording(db, cfg, rid, say=print):
    """Embed the recording's passages whose text changed since they were last embedded, update the ones where only
    who or where changed, and drop the ones it no longer has. Returns (embedded, kept). EmbedError when the server
    can't embed them."""
    ps = build(db, cfg, rid)
    st = state(db) or {}
    same = st.get("ident") == ident(cfg)
    have = {r["id"]: r for r in db.rows("SELECT record::id(id) AS id, hash, meta FROM passage WHERE recording = $r", r=rid)} if same else {}
    todo = [p for p in ps if (have.get(p["key"]) or {}).get("hash") != p["hash"]]
    moved = [p for p in ps if p not in todo and have[p["key"]].get("meta") != p["meta"]]
    for p in moved:  # same text, so the same vector: only the passage's speakers, times or page changed
        fields = {x: v for x, v in p.items() if x != "key"}
        db.q("UPDATE $r SET " + ", ".join(f"{x} = ${x}" for x in fields), r=R("passage", p["key"]), **fields)
    batch = max(1, int(_section(cfg).get("batch_size") or 32))
    prefix = prefixes(cfg)[1]
    for k in range(0, len(todo), batch):
        part = todo[k : k + batch]
        vecs = _embed_passages(db, cfg, [prefix + p["text"] for p in part])
        ensure_index(db, cfg, len(vecs[0]))
        rows = [{**store.clean({x: v for x, v in p.items() if x != "key"}), "embedding": vec} for p, vec in zip(part, vecs)]
        db.run(
            [f"UPSERT $r{i} CONTENT $d{i}" for i in range(len(rows))],
            **{f"r{i}": R("passage", p["key"]) for i, p in enumerate(part)},
            **{f"d{i}": d for i, d in enumerate(rows)},
        )
        if k + batch < len(todo):
            say(f"embedded {k + len(part)} of {len(todo)} passages")
    keys = {p["key"] for p in ps}
    gone = [k for k in have if k not in keys]
    if gone:
        db.q("DELETE passage WHERE recording = $r AND record::id(id) IN $g", r=rid, g=gone)
    if todo:
        recovered(db)
    if (state(db) or {}).get("ident") != ident(cfg) and todo:  # another process switched models meanwhile: index again
        raise EmbedError("the embeddings model changed while indexing")
    db.q("UPDATE $r SET embedded = $e", r=R("recording", rid), e={"ident": ident(cfg), "passages": len(ps), "at": store.now()})
    return len(todo), len(ps) - len(todo)


def forget(db, rids):
    """Mark recordings as needing to be indexed again (their text or speakers changed outside a pipeline)."""
    if rids:
        db.q("UPDATE recording SET embedded = NONE WHERE id IN $ids", ids=[R("recording", int(r)) for r in rids])


def unindexed(db, cfg, spaces, limit):
    """Recordings in these namespaces whose passages aren't the configured model's yet, oldest first."""
    return [
        r["id"]
        for r in db.rows(
            "SELECT record::id(id) AS id FROM recording WHERE space IN $s AND status NOT IN ['new', 'error'] "
            "AND (embedded = NONE OR embedded.ident != $i) ORDER BY id LIMIT $n",
            s=spaces,
            i=ident(cfg),
            n=limit,
        )
    ]


def index_pending(db, cfg, ns=None, limit=0, force=False, log=print):
    """Index recordings not yet indexed with the configured model (with force, every recording) in one namespace or
    all of them, here and now (`lens embed`). The number indexed."""
    if not configured(cfg):
        raise SystemExit(
            "search by meaning is off, or has no embeddings server and model (embeddings in archive.yaml, or Settings → Search)"
        )
    recovered(db)  # asked for now: try the server even if it failed a moment ago
    try:
        spaces = [store.ns_id(db, ns, create=False)] if ns else sorted(store.space_names(db))
    except KeyError:
        raise SystemExit(f"there's no namespace {ns!r}") from None
    if force:
        q = "SELECT record::id(id) AS id FROM recording WHERE space IN $s AND status NOT IN ['new', 'error'] ORDER BY id"
        rids = [r["id"] for r in db.rows(q, s=spaces)]
    else:
        rids = unindexed(db, cfg, spaces, 10**9)
    rids = rids[: limit or None]
    for rid in rids:
        made, kept = index_recording(db, cfg, rid, log)
        log(f"  {rid}: {made} passage(s) embedded" + (f", {kept} unchanged" if kept else ""))
    return len(rids)


# ---------- searching ----------
def query_text(q):
    """A search as plain words for the model: no quotes, and OR between alternatives dropped."""
    parts = [p for p in re.findall(r'[^\s"]+', q or "") if p.upper() != "OR"]
    return " ".join(parts).strip()


def nearest(db, cfg, q, where_f, params, kinds, k):
    """The passages most like the query, most alike first, each with its `similarity`; only those at least
    `floor(cfg)` alike, and no more than SPREAD less alike than the closest. `where_f` filters passages (" AND ..."
    on space, recording, speakers, emotions)."""
    text = query_text(q)
    if not text or not kinds:
        return []
    vec = query_vector(cfg, text)
    dim = (state(db) or {}).get("dimension")
    if dim and len(vec) != dim:
        raise EmbedError(f"the model's vectors now have {len(vec)} dimensions, the index {dim}: index again (lens embed --force)")
    p = {**params, "vec": vec, "kinds": sorted(kinds)}
    rows = None
    if not db.embedded:  # the embedded engine's vector index drops filtered rows (SurrealDB 2.x): it compares them all
        try:
            rows = db.rows(
                f"SELECT {FIELDS}, speakers, emotions, vector::distance::knn() AS d FROM passage "
                f"WHERE embedding <|{int(k)},{max(40, min(800, k * 2))}|> $vec AND kind IN $kinds{where_f} ORDER BY d",
                **p,
            )
            for r in rows:
                r["similarity"] = 1 - float(r.pop("d"))
        except Exception as e:  # noqa: BLE001 - no index (yet): compare every passage
            log.debug("search by meaning: the vector index wasn't used (%s)", e)
            rows = None
    if rows is None:
        try:
            rows = db.rows(
                f"SELECT {FIELDS}, speakers, emotions, vector::similarity::cosine(embedding, $vec) AS similarity FROM passage "
                f"WHERE embedding != NONE AND kind IN $kinds{where_f} ORDER BY similarity DESC LIMIT {int(k)}",
                **p,
            )
        except Exception as e:  # noqa: BLE001 - e.g. vectors of another length: the words still answer
            log.warning("search by meaning failed: %s", e)
            raise EmbedError("the passages couldn't be compared") from None
    rows = [r for r in rows if r.get("similarity") is not None]
    least = max([floor(cfg)] + [r["similarity"] - SPREAD for r in rows[:1]])
    return [r for r in rows if r["similarity"] >= least]
