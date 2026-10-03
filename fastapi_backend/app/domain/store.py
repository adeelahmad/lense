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


# Audio and video the folder scans import, and the types uploads accept unless changed.
MEDIA_EXT = (".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".mp4", ".webm", ".amr", ".mov", ".mkv", ".m4v", ".avi")
# documents and images, which uploads also accept (domain/documents.py): PDFs, and files made into PDFs to read
# (domain/convert.py): Office and OpenDocument files, text and Markdown, saved web pages, and emails
OFFICE_EXT = (".doc", ".docx", ".odt", ".rtf", ".ppt", ".pptx", ".odp", ".xls", ".xlsx", ".ods")
TEXT_EXT = (".txt", ".text", ".md", ".markdown", ".mdx")
PAGE_EXT = (".html", ".htm")
EMAIL_EXT = (".eml", ".msg")
DOCUMENT_EXT = (".pdf", *OFFICE_EXT, *TEXT_EXT, *PAGE_EXT, *EMAIL_EXT)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp")
DEFAULTS = {
    "data_dir": "./archive-data",
    "database": {"url": None, "namespace": "archive", "database": "main", "user": "root", "password": "root"},
    "namespaces": {},
    "audio": {"extensions": list(MEDIA_EXT), "path_map": {}},
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
    # chat_models: the models people may pick in Chat; empty: whatever the model server lists (docs/configuration.md)
    "llm": {
        "base_url": None,
        "model": None,
        "api_key_env": None,
        "max_chars": 24000,
        "timeout": 300,
        "chat_models": [],
        # a model the admin knows can see images: it describes pages and shots (the describe step); none, none are
        "vision_model": None,
        "describe_max": 50,  # pages or shots of a resource described at most
    },
    "graph": {"max_nodes": 150, "min_edge_weight": 2},
    "search": {"stemming": "english"},
    # search by meaning (app/domain/semantic.py): an OpenAI-compatible embeddings server (null: the LLM provider's),
    # the model, how long passages are, and how alike a passage must be to a query (null: what suits the model)
    "embeddings": {
        "enabled": True,
        "base_url": None,
        "model": "nomic-embed-text",
        "api_key_env": None,
        "query_prefix": None,
        "document_prefix": None,
        "passage_chars": 800,
        "batch_size": 32,
        "neighbours": 40,
        "min_similarity": None,
        "timeout": 60,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8770,
        "allowed_hosts": ["127.0.0.1", "localhost"],
        "embed_frame_ancestors": ["'self'"],
        "max_upload_mb": 50,
        "session_hours": 168,
        "secure_cookies": False,
        # proxies whose X-Forwarded-For names the visitor's address, for IP groups (docs/configuration.md)
        "trusted_proxies": ["127.0.0.0/8", "::1/128"],
    },
    # how long API keys last (docs/configuration.md): what a new key gets, the most it may get, and whether keys may
    # never expire; and how long the tokens of apps given access through OAuth last (domain/oauth.py): the access token,
    # and the grant after the app last renewed it
    "tokens": {"default_days": 90, "max_days": 365, "never_expire": False, "oauth_access_minutes": 60, "oauth_refresh_days": 30},
    # audio, video, documents and images uploaded in the web app, in pieces (docs/configuration.md); transcript files use
    # server.max_upload_mb
    "uploads": {"max_mb": 4096, "extensions": list(MEDIA_EXT + DOCUMENT_EXT + IMAGE_EXT), "chunk_mb": 8, "expire_hours": 24},
    # documents and images (docs/configuration.md): how large their pages are drawn, when a page is read by OCR, and
    # how many pages are read at most
    "documents": {
        "page_pixels": 2000,
        "thumb_pixels": 360,
        "ocr_below_chars": 25,
        "max_pages": 2000,
        "convert_seconds": 300,
        "attachment_resources": True,
        "soffice": None,
        "chromium": None,
        "web_networks": [],
    },
    "workers": {
        "inline": 1,
        "poll_seconds": 2,
        "stale_minutes": 15,
        "max_attempts": 3,
        "steps": [
            "transcribe",
            "diarize",
            "shots",
            "ocr",
            "faces",
            "objects",
            "describe",
            "analyze",
            "embed",
            "summarize",
            "llm",
            "report",
            "export",
            "workflow",
        ],
    },
    # video: sampling, shot detection, OCR and faces. Model paths are bootstrap-only (the app can't point at arbitrary files).
    # the chat assistant's tools, and the double check before batch runs
    "ai": {
        "tools": True,
        "extensions": True,  # tools, skills, hooks and plugins people add (extensions.py)
        "disabled_tools": [],
        "max_steps": 6,
        "max_transcript_reads": 20,
        "confirm_over_recordings": 100,
        "confirm_over_cost": None,
        "price_in": None,
        "price_out": None,
    },
    # talking to Lens (voice.py): input auto uses the server's speech-to-text engine when it has one, else the
    # browser's; spoken answers come from tts_model (an OpenAI-compatible /audio/speech), else the browser reads them
    "voice": {"input": "auto", "tts_base_url": None, "tts_model": None, "tts_voice": None, "tts_api_key": None},
    # what Lens fetches for itself (components.py): auto fetches what the settings need; also names optional ones
    "components": {"auto": True, "also": []},
    # routine decisions the assistant takes instead of asking (decide.py): engine auto uses the decision model when it
    # has a key, else the language model. act_above: the confidence it acts on; below it, it asks.
    "decisions": {
        "engine": "auto",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "api_key": None,
        "act_above": 0.8,
        "timeout": 10,
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
        "object_engine": "yolox",
        "yolox_model": None,
        "ultralytics_model": None,
        "object_min_score": 0.4,
    },
    # rclone and local_roots are bootstrap-only on purpose: the web app must not be able to pick an executable
    # or open up arbitrary folders on the server. Local folders can only be watched inside local_roots.
    "sources": {"rclone": None, "local_roots": [], "check_seconds": 15, "cache_dir": None},
    "reports": {"audio": "link"},
    # sensors (sensors.py, docs/sensors.md): off until an admin turns them on. Then the MQTT hub and the syslog listener
    # run in the process that runs routines (`lens worker`), on these ports; syslog is taken only from syslog_networks.
    # store, raw_days, rollup_days, important_days and max_per_minute are what a stream sensor gets unless it has its own.
    # bind is a startup setting only.
    "sensors": {
        "enabled": False,
        "bind": "0.0.0.0",
        "mqtt": True,
        "mqtt_port": 1883,
        "mqtt_anonymous": False,
        "syslog": True,
        "syslog_port": 5514,
        "syslog_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128"],
        "max_payload_kb": 256,
        "store": "all",
        "raw_days": 30,
        "rollup_days": 365,
        "important_days": 180,
        "max_per_minute": 600,
        "triage": False,
    },
    # notifications to webhooks and Matterbridge (docs/notifications.md): targets reach public addresses only, and the
    # private networks listed here (a Matterbridge on the LAN or the Docker network); app_url is where links in messages
    # point (null: FRONTEND_URL)
    # outgoing email (app/email.py): access requests and password resets. MAIL_* in .env set them instead, locked.
    "mail": {
        "server": None,
        "port": 587,
        "username": None,
        "password": None,
        "from_address": None,
        "from_name": "Lens",
        "security": "starttls",
    },
    "notifications": {"enabled": True, "networks": [], "poll_seconds": 5, "max_attempts": 6, "app_url": None},
    # OpenTelemetry traces and metrics (docs/telemetry.md): off unless an admin turns it on, and sent only to the OTLP/HTTP
    # endpoint set here (e.g. a collector at http://localhost:4318). headers is a secret: key=value pairs for the
    # endpoint's auth. prices: {model: {input, output}} in USD per million tokens, for cost estimates.
    "telemetry": {
        "enabled": False,
        "endpoint": None,
        "headers": None,
        "traces": True,
        "metrics": True,
        "sample_ratio": 1.0,
        "export_seconds": 60,
        "service_name": "lens",
        "prices": {},
    },
    # Fedora (docs/fedora.md): a copy of the archive in a Fedora 6 repository, off until url is set (enabled: false pauses
    # it). password is a
    # secret; files: send recordings' files too (up to max_file_mb each, 0: any size).
    "fedora": {
        "enabled": True,
        "url": None,
        "user": None,
        "password": None,
        "root": "lens",
        "files": True,
        "max_file_mb": 0,
        "sync_seconds": 60,
        "full_hours": 24,
    },
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
    if extra := os.environ.get("LENS_WEB_NETWORKS"):  # for Docker and the packages, whose archive.yaml is in the image
        cfg["documents"]["web_networks"] = [*(cfg["documents"].get("web_networks") or []), *extra.replace(",", " ").split()]
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

        attempt = 0
        while True:
            c = Surreal(self.url)
            try:
                if not self.embedded:
                    c.signin(self._creds)
                c.use(*self._target)  # on a server this creates the database, and two at once can conflict
                return c
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    c.close()
                if attempt == RETRIES or not _retryable(e):
                    raise
                _backoff(attempt)
                attempt += 1

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
                    for index, table in TEXT_INDEXES:
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
    # counters (next_id), the migration marker and the settings version. Defined up front: SurrealDB 3 refuses to
    # SELECT from a table nobody has written to yet ("table 'seq' does not exist"), which a fresh database with no
    # namespaces in archive.yaml would otherwise hit in migrate() before anything had created it.
    "DEFINE TABLE IF NOT EXISTS seq SCHEMALESS",
    # first-run setup (domain/setup.py): setup:wizard while the web wizard is still to be finished
    "DEFINE TABLE IF NOT EXISTS setup SCHEMALESS",
    # No composite indexes: on SurrealDB 2.x a (space, x) index makes "space = $s" lookups return nothing, so
    # uniqueness is enforced on single "<space>:<value>" key fields instead.
    "DEFINE TABLE IF NOT EXISTS space SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS space_name ON space FIELDS name UNIQUE",
    # a namespace's collections (domain/hierarchy.py): every recording lives in one; key is "<space>:<parent>:<name>"
    "DEFINE TABLE IF NOT EXISTS collection SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS collection_space ON collection FIELDS space",
    "DEFINE INDEX IF NOT EXISTS collection_parent ON collection FIELDS parent",
    "DEFINE INDEX IF NOT EXISTS collection_key ON collection FIELDS key UNIQUE",
    # roles people were given on collections (hierarchy.give): id "<collection>-<account>"
    "DEFINE TABLE IF NOT EXISTS collection_role SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS collection_role_account ON collection_role FIELDS account",
    "DEFINE INDEX IF NOT EXISTS collection_role_collection ON collection_role FIELDS collection",
    "DEFINE TABLE IF NOT EXISTS recording SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS recording_space ON recording FIELDS space",
    "DEFINE INDEX IF NOT EXISTS recording_collection ON recording FIELDS collection",
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
    # pairs of speakers someone said aren't the same person: never suggested again (not_same:⟨a-b⟩, a < b)
    "DEFINE TABLE IF NOT EXISTS not_same SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS merge SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS section SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS section_rec ON section FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS entity SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_space ON entity FIELDS space",
    "DEFINE INDEX IF NOT EXISTS entity_key ON entity FIELDS ekey UNIQUE",
    # how a namespace (or one of its collections) organises its entities, and its own entity types (entity_setup.py)
    "DEFINE TABLE IF NOT EXISTS entity_scope SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_scope_space ON entity_scope FIELDS space",
    "DEFINE TABLE IF NOT EXISTS entity_kind SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_kind_space ON entity_kind FIELDS space",
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
    # each namespace's data key, wrapped by the keys that can open it (app/domain/keyring.py)
    "DEFINE TABLE IF NOT EXISTS data_key SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS account SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS account_email ON account FIELDS email UNIQUE",
    "DEFINE TABLE IF NOT EXISTS membership SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS membership_account ON membership FIELDS account",
    "DEFINE INDEX IF NOT EXISTS membership_space ON membership FIELDS space",
    # permission on one recording for someone without a role in its namespace (docs/access.md): permission:<rid>-<account>
    "DEFINE TABLE IF NOT EXISTS permission SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS permission_account ON permission FIELDS account",
    "DEFINE INDEX IF NOT EXISTS permission_recording ON permission FIELDS recording",
    # someone asking for permission on a recording: access_request:<rid>-<account>, the latest request only
    "DEFINE TABLE IF NOT EXISTS access_request SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS access_request_recording ON access_request FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS access_request_status ON access_request FIELDS status",
    # the path, fingerprint and remote file of a recording deleted from a namespace or moved out of it, which that
    # namespace's scans and watched folders skip (domain/deletion.py)
    "DEFINE TABLE IF NOT EXISTS gone_recording SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS gone_recording_space ON gone_recording FIELDS space",
    # address ranges whose visitors see all of a namespace's recordings, or chosen ones (docs/access.md): ip_group:<n>
    "DEFINE TABLE IF NOT EXISTS ip_group SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS ip_group_space ON ip_group FIELDS space",
    # audio and video arriving in pieces (docs/api.md, Uploads): upload:<random id>
    "DEFINE TABLE IF NOT EXISTS upload SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS upload_account ON upload FIELDS account",
    "DEFINE TABLE IF NOT EXISTS login_session SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS login_session_account ON login_session FIELDS account",
    "DEFINE INDEX IF NOT EXISTS login_session_sid ON login_session FIELDS sid",
    "DEFINE TABLE IF NOT EXISTS password_reset SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS password_reset_account ON password_reset FIELDS account",
    "DEFINE TABLE IF NOT EXISTS api_token SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS api_token_hash ON api_token FIELDS hash UNIQUE",
    "DEFINE INDEX IF NOT EXISTS api_token_account ON api_token FIELDS account",
    # OAuth (app/domain/oauth.py): apps that registered, one-time codes, the access people gave them, and its tokens
    # (oauth_client:<client id>; oauth_code and oauth_token by the hash of the code or token)
    "DEFINE TABLE IF NOT EXISTS oauth_client SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS oauth_code SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS oauth_grant SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS oauth_grant_account ON oauth_grant FIELDS account",
    "DEFINE TABLE IF NOT EXISTS oauth_token SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS oauth_token_gid ON oauth_token FIELDS gid",
    "DEFINE TABLE IF NOT EXISTS share_link SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS share_link_rec ON share_link FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS share_link_short ON share_link FIELDS short",
    # sites whose pages framed a share link's player (share_embed:⟨link-site⟩): opens and when
    "DEFINE TABLE IF NOT EXISTS share_embed SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS share_embed_rec ON share_embed FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS share_embed_share ON share_embed FIELDS share",
    "DEFINE TABLE IF NOT EXISTS audit_log SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS audit_at ON audit_log FIELDS at",
    # background work
    "DEFINE TABLE IF NOT EXISTS job SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS job_status ON job FIELDS status",
    "DEFINE INDEX IF NOT EXISTS job_rec ON job FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS job_updated ON job FIELDS updated_at",
    "DEFINE INDEX IF NOT EXISTS job_finished ON job FIELDS finished_at",
    "DEFINE INDEX IF NOT EXISTS recording_created ON recording FIELDS created_at",
    # notifications (domain/notify.py): a namespace's targets (notify_target:<n>), what each was sent
    # (notify_delivery:<random>), the events claimed for sending (notify_event:<hash of its key>) and where the
    # notifier's next look starts (notify_state:scan)
    "DEFINE TABLE IF NOT EXISTS notify_target SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS notify_target_space ON notify_target FIELDS space",
    "DEFINE TABLE IF NOT EXISTS notify_delivery SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS notify_delivery_target ON notify_delivery FIELDS target",
    "DEFINE INDEX IF NOT EXISTS notify_delivery_status ON notify_delivery FIELDS status",
    "DEFINE TABLE IF NOT EXISTS notify_event SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS notify_state SCHEMALESS",
    # every line of a run's log, in chunks (jobs.RunLog): job_log:<random>
    "DEFINE TABLE IF NOT EXISTS job_log SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS job_log_job ON job_log FIELDS job",
    # the last few times each kind of step took, for estimates (jobs.estimates): step_stat:<type> and :<type:template>
    "DEFINE TABLE IF NOT EXISTS step_stat SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS worker SCHEMALESS",
    # storage
    "DEFINE TABLE IF NOT EXISTS storage_source SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS watch_path SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS watch_path_source ON watch_path FIELDS source",
    "DEFINE TABLE IF NOT EXISTS remote_file SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS remote_file_watch ON remote_file FIELDS watch",
    # sensors (sensors.py): stream sensors are storage_source rows too, found by their key (mqtt:<prefix>,
    # syslog:<address>, webhook:<id>) or a webhook's token hash; their streams (sensor_stream:<sensor>-<hash>), readings,
    # hourly rollups (sensor_rollup:<stream>-<field>-<hour>), hub logins and the processes running the hub
    "DEFINE INDEX IF NOT EXISTS storage_source_key ON storage_source FIELDS key",
    "DEFINE INDEX IF NOT EXISTS storage_source_push ON storage_source FIELDS push_hash",
    "DEFINE TABLE IF NOT EXISTS sensor_stream SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_stream_sensor ON sensor_stream FIELDS sensor",
    "DEFINE TABLE IF NOT EXISTS sensor_reading SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_reading_sensor ON sensor_reading FIELDS sensor, at",
    "DEFINE INDEX IF NOT EXISTS sensor_reading_stream ON sensor_reading FIELDS stream, at",
    "DEFINE TABLE IF NOT EXISTS sensor_rollup SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_rollup_stream ON sensor_rollup FIELDS stream, field, hour",
    "DEFINE INDEX IF NOT EXISTS sensor_rollup_sensor ON sensor_rollup FIELDS sensor, hour",
    # a log stream's patterns (sensor_pattern:<stream>-<hash of the template>): counts, a label (routine, notable,
    # alert: by the decision model or a person) and an action (drop: counted, not kept)
    "DEFINE TABLE IF NOT EXISTS sensor_pattern SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_pattern_sensor ON sensor_pattern FIELDS sensor, stream",
    "DEFINE INDEX IF NOT EXISTS sensor_pattern_stream ON sensor_pattern FIELDS stream",
    "DEFINE INDEX IF NOT EXISTS sensor_pattern_action ON sensor_pattern FIELDS action",
    "DEFINE INDEX IF NOT EXISTS sensor_reading_pattern ON sensor_reading FIELDS pattern",
    "DEFINE TABLE IF NOT EXISTS sensor_login SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_login_name ON sensor_login FIELDS username UNIQUE",
    "DEFINE TABLE IF NOT EXISTS sensor_service SCHEMALESS",
    # templates, pipelines, outputs, chat, edits
    "DEFINE TABLE IF NOT EXISTS template SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS template_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS template_version_t ON template_version FIELDS template",
    "DEFINE TABLE IF NOT EXISTS pipeline SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS pipeline_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS pipeline_version_p ON pipeline_version FIELDS pipeline",
    "DEFINE TABLE IF NOT EXISTS workflow SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS content_type SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS workflow_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS workflow_version_w ON workflow_version FIELDS workflow",
    # custom nodes: bodies of nodes saved under a name, used in workflows (custom_nodes.py)
    "DEFINE TABLE IF NOT EXISTS custom_node SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS custom_node_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS custom_node_version_n ON custom_node_version FIELDS node",
    # extensions: tools, skills, hooks and plugins added to the assistant (extensions.py)
    "DEFINE TABLE IF NOT EXISTS extension SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS extension_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS extension_version_e ON extension_version FIELDS extension",
    # routines (scheduled syncs, pipelines and workflows) and the graph changes they make or propose
    "DEFINE TABLE IF NOT EXISTS seed SCHEMALESS",  # what has been seeded once: seed:routines
    "DEFINE TABLE IF NOT EXISTS routine SCHEMALESS",
    # Fedora (fedora.py): what to send, what was sent (a hash per resource path) and how the last sync went
    "DEFINE TABLE IF NOT EXISTS fedora_outbox SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS fedora_state SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS fedora_status SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS routine_run SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS routine_run_r ON routine_run FIELDS routine",
    "DEFINE TABLE IF NOT EXISTS graph_change SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS graph_change_run ON graph_change FIELDS run",
    "DEFINE INDEX IF NOT EXISTS graph_change_status ON graph_change FIELDS status",
    "DEFINE INDEX IF NOT EXISTS graph_change_pair ON graph_change FIELDS pair",
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
    # objects: one row per kind of object on a recording (app/domain/objects.py)
    "DEFINE TABLE IF NOT EXISTS object_track SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS object_track_rec ON object_track FIELDS recording",
    # what a model that can see images says a page or a shot shows (descriptions.py)
    "DEFINE TABLE IF NOT EXISTS description SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS description_rec ON description FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS face_merge SCHEMALESS",
    # passages embedded for search by meaning (app/domain/semantic.py), and which model's vectors they hold; their
    # HNSW index is defined when the first vector is stored, since its dimension is the model's
    "DEFINE TABLE IF NOT EXISTS passage SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS passage_rec ON passage FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS passage_space ON passage FIELDS space",
    "DEFINE TABLE IF NOT EXISTS embedding_state SCHEMALESS",
    # collections, batch runs, assistant approvals
    "DEFINE TABLE IF NOT EXISTS saved_collection SCHEMALESS",
    # saved views of the Library (app/domain/views.py)
    "DEFINE TABLE IF NOT EXISTS saved_view SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS saved_view_account ON saved_view FIELDS account",
    "DEFINE INDEX IF NOT EXISTS saved_view_space ON saved_view FIELDS space",
    # notes on recordings, yours or shared with everyone who can read it (app/domain/notes.py)
    "DEFINE TABLE IF NOT EXISTS note SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS note_rec ON note FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS note_account ON note FIELDS account",
    # comments on resources, threaded, by everyone who can read them (app/domain/comments.py)
    "DEFINE TABLE IF NOT EXISTS comment SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS comment_rec ON comment FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS comment_parent ON comment FIELDS parent",
    # passages of resources marked in colour by their editors (app/domain/highlights.py)
    "DEFINE TABLE IF NOT EXISTS highlight SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS highlight_rec ON highlight FIELDS recording",
    # custom metadata fields defined on namespaces and collections (app/domain/fields.py); key: "<space>:<collection>:<target>:<name>"
    "DEFINE TABLE IF NOT EXISTS field SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS field_space ON field FIELDS space",
    "DEFINE INDEX IF NOT EXISTS field_collection ON field FIELDS collection",
    "DEFINE INDEX IF NOT EXISTS field_key ON field FIELDS key UNIQUE",
    # a resource's supplementary files, and the lines parsed from its transcripts, captions and indexes (app/domain/files.py)
    "DEFINE TABLE IF NOT EXISTS resource_file SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS resource_file_rec ON resource_file FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS resource_file_space ON resource_file FIELDS space",
    # the pages of documents and images, drawn and read (app/domain/documents.py): page:⟨<resource>-<index>⟩
    "DEFINE TABLE IF NOT EXISTS page SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS page_rec ON page FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS file_line SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS file_line_file ON file_line FIELDS file",
    "DEFINE INDEX IF NOT EXISTS file_line_rec ON file_line FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS file_line_space ON file_line FIELDS space",
    "DEFINE INDEX IF NOT EXISTS saved_collection_owner ON saved_collection FIELDS account",
    "DEFINE TABLE IF NOT EXISTS batch SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS job_batch ON job FIELDS batch",
    "DEFINE TABLE IF NOT EXISTS approval SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS approval_chat ON approval FIELDS chat",
]


def _analyzer(cfg):
    stem = ", snowball(english)" if cfg["search"]["stemming"] == "english" else ""
    return f"DEFINE ANALYZER IF NOT EXISTS archive_text TOKENIZERS class FILTERS lowercase, ascii{stem}"


# the full-text indexes, each on the `text` of its table: transcripts, text on screen, and lines of supplementary files
TEXT_INDEXES = (
    ("segment_text", "segment"),
    ("ocr_text", "ocr_span"),
    ("file_text", "file_line"),
    ("object_text", "object_track"),
    ("description_text", "description"),
)


def _text_index(db):
    # SurrealDB 3 spells full-text indexes FULLTEXT; 2.x (and the embedded engine) spell them SEARCH.
    found = None
    for kw in ("FULLTEXT", "SEARCH"):
        try:
            for index, table in TEXT_INDEXES:
                db.q(f"DEFINE INDEX IF NOT EXISTS {index} ON {table} FIELDS text {kw} ANALYZER archive_text BM25 HIGHLIGHTS")
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
    from . import access, hierarchy  # each step lives with the code it serves

    return [access.migrate_legacy, hierarchy.migrate_homes]


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
    for s in [f"REMOVE INDEX IF EXISTS {index} ON {table}" for index, table in TEXT_INDEXES] + ["REMOVE ANALYZER IF EXISTS archive_text"]:
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
    try:
        db.q("CREATE $r CONTENT $d", r=R("space", sid), d={"name": name, "graph": "shared"})
    except Exception:  # noqa: BLE001 - another process made it first (the API and a worker starting on a fresh database)
        row = db.one("SELECT record::id(id) AS id FROM space WHERE name = $n LIMIT 1", n=name)
        if not row:
            raise
        return row["id"]
    default_collection(db, sid)
    return sid


DEFAULT_COLLECTION = "General"


def collection_key(sid, parent, name):
    """What makes a collection's name unique: its namespace, its parent and the name, ignoring case."""
    return f"{sid}:{parent or 0}:{' '.join(str(name).split()).casefold()}"


def default_collection(db, sid):
    """The namespace's default collection, where recordings nobody placed go: made ("General") when it has none."""
    row = db.one("SELECT default_collection FROM $r", r=R("space", sid)) or {}
    cid = row.get("default_collection")
    if cid is not None and db.one("SELECT record::id(id) AS id FROM $r", r=R("collection", cid)):
        return cid
    key = collection_key(sid, None, DEFAULT_COLLECTION)
    found = db.one("SELECT record::id(id) AS id FROM collection WHERE key = $k LIMIT 1", k=key)
    if found:
        cid = found["id"]
    else:
        cid = db.next_id("collection")
        try:
            db.q(
                "CREATE $r CONTENT $d",
                r=R("collection", cid),
                d={"space": sid, "name": DEFAULT_COLLECTION, "key": key, "created_at": now()},
            )
        except Exception:  # noqa: BLE001 - another process made it first
            cid = db.one("SELECT record::id(id) AS id FROM collection WHERE key = $k LIMIT 1", k=key)["id"]
    db.q("UPDATE $r SET default_collection = $c", r=R("space", sid), c=cid)
    return cid


def home(db, sid, collection=None):
    """The collection a new recording in namespace `sid` goes into: `collection` (KeyError unless it's one of the
    namespace's), else the namespace's default."""
    if collection is None:
        return default_collection(db, sid)
    row = db.one("SELECT space FROM $r", r=R("collection", int(collection)))
    if not row or row["space"] != sid:
        raise KeyError(collection)
    return int(collection)


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
    "DELETE passage WHERE recording = $rid",
]


def reset_downstream(db, rid):
    """Remove everything derived from a recording's transcript."""
    db.run(DOWNSTREAM + ["UPDATE $rec SET embedded = NONE"], rid=rid, keep=0, rec=R("recording", rid))


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
