"""Configuration, the SurrealDB store, labels and small shared helpers.

The archive runs against SurrealDB in one of two ways, chosen by database.url (or $SURREAL_URL):
  surrealkv://path or mem://   embedded in this process, no server needed (one process at a time)
  ws://host:8000               a SurrealDB server, shared by the web app, batch jobs and other machines
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import os
import pathlib
import logging
import queue
import random
import re
import threading
import time

EMOTIONS = [
    "Neutral",
    "Happy",
    "Joy",
    "Amusement",
    "Relief",
    "Surprise",
    "Anxiety",
    "Guilt",
    "Sad",
    "Angry",
    "Fear",
    "Disgust",
    "Shame",
    "Love",
]
PALETTE = {
    "Neutral": "#6c757d",
    "Happy": "#ffc107",
    "Sad": "#007bff",
    "Angry": "#dc3545",
    "Fear": "#6f42c1",
    "Disgust": "#e83e8c",
    "Surprise": "#17a2b8",
    "Amusement": "#fd7e14",
    "Joy": "#28a745",
    "Love": "#d63384",
    "Relief": "#20c997",
    "Anxiety": "#842029",
    "Guilt": "#495057",
    "Shame": "#343a40",
}
EMOJI = {
    "Neutral": "😐",
    "Happy": "😄",
    "Joy": "🤩",
    "Amusement": "😆",
    "Relief": "😌",
    "Anxiety": "😰",
    "Guilt": "😔",
    "Surprise": "😲",
    "Sad": "😢",
    "Angry": "😠",
    "Fear": "😨",
    "Disgust": "🤢",
    "Shame": "😳",
    "Love": "🥰",
}
SV_EMOTION = {
    "HAPPY": "Happy",
    "SAD": "Sad",
    "ANGRY": "Angry",
    "NEUTRAL": "Neutral",
    "FEARFUL": "Fear",
    "DISGUSTED": "Disgust",
    "SURPRISED": "Surprise",
}
SV_EVENTS = {"Speech", "BGM", "Applause", "Laughter", "Cry", "Sneeze", "Breath", "Cough"}
EVENT_EMOJI = {"Laughter": "😂", "Applause": "👏", "BGM": "🎵", "Cry": "😭", "Cough": "🤧", "Sneeze": "🤧"}
ALIASES = {
    "Fearful": "Fear",
    "Surprised": "Surprise",
    "Disgusted": "Disgust",
    "Sadness": "Sad",
    "Anger": "Angry",
    "Happiness": "Happy",
    "N": "Neutral",
}
SPEAKER_COLORS = ["#2F6690", "#C2571A", "#5B7F2B", "#7A4E9A", "#A23B5B", "#2E8A82", "#8C6D1F", "#4F5D75"]
API = "/api/v1"  # where the HTTP API lives; media links built here are signed by the API layer before they leave
SEG = 1_000_000  # segment id = recording id * SEG + index, so ids are stable and need no lookups


def norm_emotion(v):
    if v is None:
        return None
    s = str(v).strip()
    if s in EMOTIONS:
        return s
    if s.upper() in SV_EMOTION:
        return SV_EMOTION[s.upper()]
    if s in ALIASES:
        return ALIASES[s]
    c = s.capitalize()
    return c if c in EMOTIONS else ALIASES.get(c)


def labels():
    return {"palette": PALETTE, "emoji": EMOJI, "events": EVENT_EMOJI, "speakers": SPEAKER_COLORS}


DEFAULTS = {
    "data_dir": "./archive-data",
    "database": {"url": None, "namespace": "archive", "database": "main", "user": "root", "password": "root"},
    "namespaces": {},
    "audio": {
        "extensions": [".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".mp4", ".webm", ".amr", ".mov", ".mkv", ".m4v", ".avi"],
        "path_map": {},
    },
    "transcribe": {
        "engine": "sensevoice",
        "device": "auto",
        "language": "auto",
        "sensevoice": {"model": "iic/SenseVoiceSmall", "hub": "ms", "vad_max_segment_ms": 30000, "batch_size": 16},
        "whisper": {"model": "large-v3-turbo", "compute_type": "default"},
        "mlx_whisper": {"model": "mlx-community/whisper-large-v3-turbo"},
    },
    "diarize": {
        "engine": "auto",
        "cluster_threshold": 0.55,
        "min_speakers": None,
        "max_speakers": None,
        "pyannote": {"model": "pyannote/speaker-diarization-3.1", "token_env": "HF_TOKEN"},
    },
    "speakers": {
        "embedder": "speechbrain",
        "model": "speechbrain/spkrec-ecapa-voxceleb",
        "match_threshold": 0.5,
        "review_threshold": 0.35,
        "sample_seconds": 60,
        "cross_namespace": "suggest",
    },
    "analysis": {"entities": "rules", "spacy_model": "en_core_web_sm", "gazetteer": []},
    "llm": {"base_url": None, "model": None, "api_key_env": None, "max_chars": 24000, "timeout": 300},
    "graph": {"max_nodes": 150, "min_edge_weight": 2},
    "search": {"stemming": "english"},
    "server": {
        "host": "127.0.0.1",
        "port": 8770,
        "allowed_hosts": ["127.0.0.1", "localhost"],
        "embed_frame_ancestors": ["'self'"],
        "max_upload_mb": 50,
        "session_hours": 168,
        "secure_cookies": False,
    },
    "workers": {
        "inline": 1,
        "poll_seconds": 2,
        "stale_minutes": 15,
        "max_attempts": 3,
        "steps": ["transcribe", "diarize", "shots", "ocr", "faces", "analyze", "summarize", "llm", "report", "export"],
    },
    # video: sampling, shot detection, OCR and faces. Model paths are bootstrap-only (the app can't point at arbitrary files).
    # the chat assistant's tools, and the double check before batch runs
    "ai": {
        "tools": True,
        "disabled_tools": [],
        "max_steps": 6,
        "max_transcript_reads": 20,
        "confirm_over_recordings": 100,
        "confirm_over_cost": None,
        "price_in": None,
        "price_out": None,
    },
    "video": {
        "sample_seconds": 5,
        "scene_threshold": 0.3,
        "min_shot_seconds": 1.0,
        "frame_width": 960,
        "ocr_engine": "auto",
        "ocr_languages": ["eng"],
        "ocr_min_confidence": 60,
        "face_engine": "opencv",
        "yunet_model": None,
        "sface_model": None,
        "face_cluster_threshold": 0.6,
        "face_match_threshold": 0.45,
        "face_review_threshold": 0.3,
        "publish_faces": False,
    },
    # rclone and local_roots are bootstrap-only on purpose: the web app must not be able to pick an executable
    # or open up arbitrary folders on the server. Local folders can only be watched inside local_roots.
    "sources": {"rclone": None, "local_roots": [], "check_seconds": 15, "cache_dir": None},
    "reports": {"audio": "link"},
    # IIIF: identifiers are built from base_url (set it to the stable public HTTPS address; null: the request's address)
    "iiif": {
        "base_url": None,
        "default_language": "none",
        "layers": ["transcript", "speakers", "entities", "chapters", "screen"],
        "rights": None,
        "attribution": None,
        "provider": None,
        "viewers": [],
        "allowed_origins": ["*"],
        "token_minutes": 60,
    },
}
NS_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path=None, overrides=None):
    path = path or os.environ.get("ARCHIVE_CONFIG") or "archive.yaml"
    p = pathlib.Path(path).expanduser()
    raw = {}
    if p.exists():
        import yaml

        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg = _merge(_merge(DEFAULTS, raw), overrides or {})
    base = p.resolve().parent if p.exists() else pathlib.Path.cwd()
    cfg["data_dir"] = str((base / os.path.expanduser(cfg["data_dir"])).resolve())
    nss = {}
    for name, spec in (cfg.get("namespaces") or {}).items():
        if not NS_RX.match(str(name)):
            raise SystemExit(f"namespace names use lowercase letters, digits, - and _: {name!r}")
        spec = {"paths": spec} if isinstance(spec, list) else (spec or {})
        graph = spec.get("graph", "shared")
        if graph not in ("shared", "isolated"):
            raise SystemExit(f"namespace {name}: graph must be shared or isolated")
        nss[name] = {"paths": [str((base / os.path.expanduser(x)).resolve()) for x in spec.get("paths", [])], "graph": graph}
    cfg["namespaces"] = nss
    if cfg["search"]["stemming"] not in ("english", "none"):
        raise SystemExit("search.stemming must be english or none")
    cfg["_path"] = str(p)
    return cfg


def resolve_path(cfg, path):
    """Recordings scanned on one machine (say a Mac) can be served from another (say a container)."""
    for src, dst in (cfg["audio"].get("path_map") or {}).items():
        if path and path.startswith(src):
            return dst + path[len(src) :]
    return path


# ---------- SurrealDB ----------
def R(table, key):
    from surrealdb import RecordID

    return RecordID(table, key)


def _plain(v):
    from surrealdb import RecordID

    if isinstance(v, RecordID):
        return v.id
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


RETRIES = 8


def _retryable(err):
    """SurrealDB's optimistic transactions fail with a write conflict when two touch the same record at once."""
    m = str(err)
    return "can be retried" in m or "Transaction conflict" in m or "Write conflict" in m


def _backoff(attempt):
    time.sleep(min(0.5, 0.005 * 2**attempt) * (0.5 + random.random()))


class DB:
    """A thread-safe SurrealDB handle.

    Embedded engines (surrealkv://, mem://) allow one connection per process, so every query waits for it.
    A SurrealDB server (ws://, http://) gets a small pool of connections, so concurrent requests don't queue behind
    each other. Each query() call runs one statement on one connection; run() sends a transaction on one connection.
    Statements that lose a write conflict to a concurrent one (SurrealDB reports these as retryable) are retried.
    """

    def __init__(self, cfg, pool_size=None):
        d = cfg["database"]
        url = os.environ.get("SURREAL_URL") or d.get("url") or "surrealkv://" + str(pathlib.Path(cfg["data_dir"]) / "surrealdb")
        self.url = url
        scheme = url.split(":", 1)[0]
        self.embedded = scheme in ("mem", "memory", "surrealkv", "file")
        if scheme in ("surrealkv", "file"):
            pathlib.Path(url.split("://", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        self._creds = {
            "username": os.environ.get("SURREAL_USER") or d.get("user") or "root",
            "password": os.environ.get("SURREAL_PASS") or d.get("password") or "root",
        }
        self._target = (os.environ.get("SURREAL_NS") or d["namespace"], os.environ.get("SURREAL_DB") or d["database"])
        size = 1 if self.embedded else max(1, int(pool_size or os.environ.get("SURREAL_POOL_SIZE") or d.get("pool_size") or 8))
        self.fulltext = None  # FULLTEXT (3.x), SEARCH (2.x) or None; set by connect()
        self._text_ready, self._text_lock = not self.embedded, threading.Lock()
        self._pool: queue.LifoQueue = queue.LifoQueue()
        self._all = []
        try:
            for _ in range(size):
                c = self._open()
                self._all.append(c)
                self._pool.put(c)
        except Exception as e:  # noqa: BLE001
            self.close()
            hint = (
                " If another process has this embedded database open, stop it or point both at a SurrealDB server (SURREAL_URL=ws://...)."
                if self.embedded
                else ""
            )
            raise SystemExit(f"cannot open SurrealDB at {url}: {e}.{hint}") from None

    def _open(self):
        from surrealdb import Surreal

        c = Surreal(self.url)
        if not self.embedded:
            c.signin(self._creds)
        c.use(*self._target)
        return c

    @contextlib.contextmanager
    def conn(self):
        c = self._pool.get()
        try:
            yield c
        finally:
            self._pool.put(c)

    def q(self, sql, **v):
        for attempt in range(RETRIES + 1):
            try:
                with self.conn() as c:
                    return c.query(sql, v)
            except Exception as e:  # noqa: BLE001
                if attempt == RETRIES or not _retryable(e):
                    raise
                _backoff(attempt)

    def rows(self, sql, **v):
        r = self.q(sql, **v)
        if r is None:
            return []
        return [_plain(x) for x in (r if isinstance(r, list) else [r])]

    def one(self, sql, **v):
        r = self.rows(sql, **v)
        return r[0] if r else None

    def values(self, sql, **v):
        r = self.q(sql, **v)
        return [_plain(x) for x in (r if isinstance(r, list) else [r])] if r is not None else []

    def run(self, statements, **v):
        """Several statements as one transaction; raises if any of them fails."""
        sql = "BEGIN TRANSACTION;\n" + ";\n".join(statements) + ";\nCOMMIT TRANSACTION;"
        for attempt in range(RETRIES + 1):
            with self.conn() as c:
                raw = c.query_raw(sql, v)
            items = raw.get("result") if isinstance(raw, dict) else raw
            if isinstance(raw, dict) and raw.get("error"):
                if attempt < RETRIES and _retryable(raw["error"]):
                    _backoff(attempt)
                    continue
                raise RuntimeError(raw["error"])
            errors = [
                (n, str(it.get("result")))
                for n, it in enumerate(items or [])
                if isinstance(it, dict) and it.get("status") not in (None, "OK")
            ]
            if errors and attempt < RETRIES and any(_retryable(m) for _, m in errors):
                _backoff(attempt)  # the whole transaction was rolled back, so running it again is safe
                continue
            break
        if errors:  # report the statement that failed, not the ones skipped because of it
            n, msg = next((e for e in errors if "not executed due to a failed transaction" not in e[1]), errors[0])
            stmt = statements[n - 1] if 0 < n <= len(statements) else "?"
            raise RuntimeError(f"query failed ({stmt[:80]}): {msg[:300]}")

    def next_id(self, table):
        return int(self.values("UPSERT $r SET n += 1 RETURN VALUE n", r=R("seq", table))[0])

    def ready_fulltext(self):
        """The full-text index kind (FULLTEXT or SEARCH) once it can be trusted; None when there is none.

        The embedded engine (SurrealDB 2.x inside the Python SDK) loses postings from its SEARCH index when the
        database is closed and reopened: after a restart, searches silently miss most segments. So the first
        full-text query in each process rebuilds the index; writes made after that are indexed correctly. SurrealDB
        servers don't have the problem, so this is a no-op for them. Callers run full-text queries only through this.
        """
        if self.fulltext and not self._text_ready:
            with self._text_lock:
                if not self._text_ready:
                    t = time.time()
                    for index, table in (("segment_text", "segment"), ("ocr_text", "ocr_span")):
                        self.q(f"REBUILD INDEX IF EXISTS {index} ON {table}")
                    self._text_ready = True
                    logging.getLogger("lens").info("rebuilt the embedded full-text index in %.1fs", time.time() - t)
        return self.fulltext

    def ping(self):
        self.q("RETURN 1")
        return True

    def close(self):
        for c in self._all:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        self._all = []


SCHEMA = [
    # No composite indexes: on SurrealDB 2.x a (space, x) index makes "space = $s" lookups return nothing, so
    # uniqueness is enforced on single "<space>:<value>" key fields instead.
    "DEFINE TABLE IF NOT EXISTS space SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS space_name ON space FIELDS name UNIQUE",
    "DEFINE TABLE IF NOT EXISTS recording SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS recording_space ON recording FIELDS space",
    "DEFINE INDEX IF NOT EXISTS recording_fp ON recording FIELDS fp_key UNIQUE",
    "DEFINE INDEX IF NOT EXISTS recording_path ON recording FIELDS path",
    "DEFINE INDEX IF NOT EXISTS recording_status ON recording FIELDS status",
    "DEFINE INDEX IF NOT EXISTS recording_access ON recording FIELDS access",
    "DEFINE INDEX IF NOT EXISTS recording_featured ON recording FIELDS featured",
    "DEFINE TABLE IF NOT EXISTS segment SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS segment_rec ON segment FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS segment_space ON segment FIELDS space",
    "DEFINE INDEX IF NOT EXISTS segment_speaker ON segment FIELDS speaker",
    "DEFINE TABLE IF NOT EXISTS speaker SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS speaker_space ON speaker FIELDS space",
    "DEFINE INDEX IF NOT EXISTS speaker_label ON speaker FIELDS label_key UNIQUE",
    "DEFINE TABLE IF NOT EXISTS appearance SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS appearance_rec ON appearance FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS appearance_spk ON appearance FIELDS speaker",
    "DEFINE TABLE IF NOT EXISTS same_as TYPE RELATION IN speaker OUT speaker",
    "DEFINE TABLE IF NOT EXISTS suggestion SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS merge SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS section SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS section_rec ON section FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS entity SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_space ON entity FIELDS space",
    "DEFINE INDEX IF NOT EXISTS entity_key ON entity FIELDS ekey UNIQUE",
    "DEFINE TABLE IF NOT EXISTS mentions TYPE RELATION IN segment OUT entity",
    "DEFINE INDEX IF NOT EXISTS mentions_rec ON mentions FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS mentions_space ON mentions FIELDS space",
    "DEFINE INDEX IF NOT EXISTS mentions_entity ON mentions FIELDS entity",
    "DEFINE TABLE IF NOT EXISTS term SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS term_rec ON term FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS term_space ON term FIELDS space",
    "DEFINE INDEX IF NOT EXISTS term_term ON term FIELDS term",
    # Note: on 2.x, CONTAINS against an indexed field also returns nothing; use string::contains() there.
    # settings, people and access
    "DEFINE TABLE IF NOT EXISTS app_setting SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS account SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS account_email ON account FIELDS email UNIQUE",
    "DEFINE TABLE IF NOT EXISTS membership SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS membership_account ON membership FIELDS account",
    "DEFINE INDEX IF NOT EXISTS membership_space ON membership FIELDS space",
    # permission on one recording for someone without a role in its namespace (docs/access.md): permission:<rid>-<account>
    "DEFINE TABLE IF NOT EXISTS permission SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS permission_account ON permission FIELDS account",
    "DEFINE INDEX IF NOT EXISTS permission_recording ON permission FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS login_session SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS login_session_account ON login_session FIELDS account",
    "DEFINE INDEX IF NOT EXISTS login_session_sid ON login_session FIELDS sid",
    "DEFINE TABLE IF NOT EXISTS password_reset SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS password_reset_account ON password_reset FIELDS account",
    "DEFINE TABLE IF NOT EXISTS api_token SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS api_token_hash ON api_token FIELDS hash UNIQUE",
    "DEFINE INDEX IF NOT EXISTS api_token_account ON api_token FIELDS account",
    "DEFINE TABLE IF NOT EXISTS share_link SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS share_link_rec ON share_link FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS audit_log SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS audit_at ON audit_log FIELDS at",
    # background work
    "DEFINE TABLE IF NOT EXISTS job SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS job_status ON job FIELDS status",
    "DEFINE INDEX IF NOT EXISTS job_rec ON job FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS job_updated ON job FIELDS updated_at",
    "DEFINE TABLE IF NOT EXISTS worker SCHEMALESS",
    # storage
    "DEFINE TABLE IF NOT EXISTS storage_source SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS watch_path SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS watch_path_source ON watch_path FIELDS source",
    "DEFINE TABLE IF NOT EXISTS remote_file SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS remote_file_watch ON remote_file FIELDS watch",
    # templates, pipelines, outputs, chat, edits
    "DEFINE TABLE IF NOT EXISTS template SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS template_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS template_version_t ON template_version FIELDS template",
    "DEFINE TABLE IF NOT EXISTS pipeline SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS pipeline_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS pipeline_version_p ON pipeline_version FIELDS pipeline",
    "DEFINE TABLE IF NOT EXISTS output SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS output_rec ON output FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS chat SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS chat_account ON chat FIELDS account",
    "DEFINE TABLE IF NOT EXISTS chat_message SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS chat_message_chat ON chat_message FIELDS chat",
    "DEFINE TABLE IF NOT EXISTS segment_edit SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS segment_edit_rec ON segment_edit FIELDS recording",
    # descriptive metadata history, IIIF change discovery and IIIF authorization
    "DEFINE TABLE IF NOT EXISTS meta_edit SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS meta_edit_target ON meta_edit FIELDS target",
    "DEFINE TABLE IF NOT EXISTS iiif_activity SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS iiif_cookie SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS iiif_token SCHEMALESS",
    # entity curation: aliases from merges, per-mention overrides, not-the-same pairs, merge snapshots, cross-namespace links
    "DEFINE TABLE IF NOT EXISTS entity_alias SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_alias_space ON entity_alias FIELDS space",
    "DEFINE INDEX IF NOT EXISTS entity_alias_entity ON entity_alias FIELDS entity",
    "DEFINE TABLE IF NOT EXISTS entity_override SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_override_rec ON entity_override FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS entity_distinct SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS entity_merge SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS entity_link SCHEMALESS",
    # video: shots, text on screen, faces
    "DEFINE TABLE IF NOT EXISTS shot SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS shot_rec ON shot FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS ocr_span SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS ocr_span_rec ON ocr_span FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS ocr_span_space ON ocr_span FIELDS space",
    "DEFINE TABLE IF NOT EXISTS face SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS face_space ON face FIELDS space",
    "DEFINE INDEX IF NOT EXISTS face_label ON face FIELDS label_key UNIQUE",
    "DEFINE TABLE IF NOT EXISTS face_track SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS face_track_rec ON face_track FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS face_track_face ON face_track FIELDS face",
    "DEFINE TABLE IF NOT EXISTS face_suggestion SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS face_merge SCHEMALESS",
    # collections, batch runs, assistant approvals
    "DEFINE TABLE IF NOT EXISTS saved_collection SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS saved_collection_owner ON saved_collection FIELDS account",
    "DEFINE TABLE IF NOT EXISTS batch SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS job_batch ON job FIELDS batch",
    "DEFINE TABLE IF NOT EXISTS approval SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS approval_chat ON approval FIELDS chat",
]


def _analyzer(cfg):
    stem = ", snowball(english)" if cfg["search"]["stemming"] == "english" else ""
    return f"DEFINE ANALYZER IF NOT EXISTS archive_text TOKENIZERS class FILTERS lowercase, ascii{stem}"


def _text_index(db):
    # SurrealDB 3 spells full-text indexes FULLTEXT; 2.x (and the embedded engine) spell them SEARCH.
    found = None
    for kw in ("FULLTEXT", "SEARCH"):
        try:
            db.q(f"DEFINE INDEX IF NOT EXISTS segment_text ON segment FIELDS text {kw} ANALYZER archive_text BM25 HIGHLIGHTS")
            db.q(f"DEFINE INDEX IF NOT EXISTS ocr_text ON ocr_span FIELDS text {kw} ANALYZER archive_text BM25 HIGHLIGHTS")
            return kw
        except Exception:  # noqa: BLE001
            continue
    return found


def connect(cfg):
    db = DB(cfg)
    db.q(_analyzer(cfg))
    for s in SCHEMA:
        db.q(s)
    db.fulltext = _text_index(db)
    for name, spec in cfg["namespaces"].items():
        sid = ns_id(db, name)
        db.q("UPDATE $r SET graph = $g", r=R("space", sid), g=spec["graph"])
    migrate(db)
    return db


def _migrations():
    from . import access  # each step lives with the code it serves

    return [access.migrate_legacy]


def migrate(db):
    """Data rewrites that run once per database, in order. The last one done is kept in seq:migrations; each step is
    safe to repeat, so two processes starting together, or a start that stops half way, do no harm."""
    done = int((db.one("SELECT n FROM $r", r=R("seq", "migrations")) or {}).get("n") or 0)
    for n, step in enumerate(_migrations(), 1):
        if n > done:
            step(db)
            db.q("UPSERT $r SET n = $n", r=R("seq", "migrations"), n=n)


def reindex(db, cfg):
    """Rebuild the full-text index, e.g. after changing search.stemming."""
    for s in (
        "REMOVE INDEX IF EXISTS segment_text ON segment",
        "REMOVE INDEX IF EXISTS ocr_text ON ocr_span",
        "REMOVE ANALYZER IF EXISTS archive_text",
    ):
        try:
            db.q(s)
        except Exception:  # noqa: BLE001
            pass
    db.q(_analyzer(cfg))
    db.fulltext = _text_index(db)
    db._text_ready = True  # just built from the current data


def ns_id(db, name, create=True):
    row = db.one("SELECT record::id(id) AS id FROM space WHERE name = $n LIMIT 1", n=name)
    if row:
        return row["id"]
    if not create:
        raise KeyError(name)
    if not NS_RX.match(name or ""):
        raise SystemExit(f"namespace names use lowercase letters, digits, - and _: {name!r}")
    sid = db.next_id("space")
    db.q("CREATE $r CONTENT $d", r=R("space", sid), d={"name": name, "graph": "shared"})
    return sid


def space_names(db):
    return {r["id"]: r["name"] for r in db.rows("SELECT record::id(id) AS id, name FROM space")}


# Segments past $keep are deleted; callers overwrite the ones they keep. Never delete and re-create the same record id in one
# transaction: SurrealDB 3.2 silently drops records re-created that way (and INSERT fails with "already exists").
DOWNSTREAM = [
    "DELETE mentions WHERE recording = $rid",
    "DELETE term WHERE recording = $rid",
    "DELETE section WHERE recording = $rid",
    "DELETE appearance WHERE recording = $rid",
    "DELETE segment WHERE recording = $rid AND idx >= $keep",
]


def reset_downstream(db, rid):
    """Remove everything derived from a recording's transcript."""
    db.run(DOWNSTREAM, rid=rid, keep=0)


def clean(d):
    return {k: v for k, v in d.items() if v is not None}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def tc(ms):
    s = max(0, int((ms or 0) // 1000))
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


class Busy(RuntimeError):
    pass


@contextlib.contextmanager
def lock(cfg, name):
    """One batch run at a time on this machine; a second run of the same step exits instead of doubling up."""
    d = pathlib.Path(cfg["data_dir"]) / "locks"
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f"{name}.lock", "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(name) from None
        yield
    finally:
        f.close()
