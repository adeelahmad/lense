"""Settings changed in the web app: stored in SurrealDB, with secrets encrypted at rest.

Precedence, lowest first: built-in defaults, archive.yaml, settings saved in the app, then the environment. The
environment only holds what can't live in the database it unlocks (the connection, ARCHIVE_SECRET_KEY) and a
break-glass override for allowed hosts (ARCHIVE_ALLOWED_HOSTS), so a bad setting can't lock everyone out.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import secrets
import threading
import urllib.parse

from . import convert, ipgroups, logs, mail_routes, objects, store, telemetry

R = store.R
EDITABLE = {
    "transcribe": None,
    "diarize": None,
    "speakers": None,
    "analysis": None,
    "llm": None,
    "graph": None,
    "search": None,
    "embeddings": None,
    "reports": None,
    "workers": None,
    "iiif": None,
    "ai": None,
    "decisions": None,
    "components": None,
    "voice": None,
    "speech": None,
    "podcasts": None,
    # llama-server's path is a startup setting only (the web app can't choose what the server runs)
    "local_llm": ("enabled", "model", "use_as_provider", "context", "threads", "gpu_layers", "port", "host"),
    "mail": None,
    "bridge": None,
    "telegram": None,
    "slack": None,
    "mailbox": None,
    "matterbridge": None,
    "notifications": None,
    "telemetry": None,
    "analytics": None,
    "logs": None,
    # repo is a startup setting only (what gets installed isn't chosen in the web app)
    "updates": ("check", "check_hours", "auto_install", "email"),
    "activity": None,
    "events": None,
    "automations": None,
    "backups": None,
    "encryption": None,
    "fedora": None,
    "tunnel": None,
    "https": None,
    # bind is a startup setting only
    "nfs": ("enabled", "port", "allowed_networks", "account"),
    # bind is a startup setting only
    # the SMB share of the file tree (smb_share.py): bind and port are startup settings only (Docker publishes the port)
    "smb": ("enabled", "allowed_networks"),
    "sensors": (
        "enabled",
        "mqtt",
        "mqtt_port",
        "mqtt_anonymous",
        "syslog",
        "syslog_port",
        "syslog_networks",
        "max_payload_kb",
        "store",
        "raw_days",
        "rollup_days",
        "important_days",
        "max_per_minute",
        "triage",
    ),
    # bind is a startup setting only
    "shares": ("enabled", "s3", "s3_port", "webdav", "webdav_port"),
    "video": (
        "sample_seconds",
        "scene_threshold",
        "min_shot_seconds",
        "frame_width",
        "ocr_engine",
        "ocr_languages",
        "ocr_min_confidence",
        "face_engine",
        "face_cluster_threshold",
        "face_match_threshold",
        "face_review_threshold",
        "publish_faces",
        # the model files (yolox_model, ultralytics_model) are startup settings only, like yunet_model
        "object_engine",
        "object_min_score",
    ),
    "nsfw": None,
    "server": ("embed_frame_ancestors", "max_upload_mb", "allowed_hosts", "session_hours", "secure_cookies", "trusted_proxies"),
    "uploads": None,
    "files": None,
    "tokens": None,
    "auth": ("passwords",),
    # the LibreOffice, Chromium and anytopdf paths are startup settings only (the web app can't choose what the server runs)
    "documents": (
        "page_pixels",
        "thumb_pixels",
        "ocr_below_chars",
        "max_pages",
        "convert_seconds",
        "attachment_resources",
        "video_urls",
        "converter",
        "anytopdf_url",
        "anytopdf_token",
        "tika_url",
        "tika_text",
        "tika_metadata",
        "web_networks",
    ),
}
MAIL_SECURITY = ("ssl", "starttls", "none")
SECRETS = {
    "search": ("opensearch_password",),
    "llm": ("api_key",),
    "embeddings": ("api_key",),
    "decisions": ("api_key",),
    "voice": ("tts_api_key",),
    "speech": ("openai_api_key", "elevenlabs_api_key", "assemblyai_api_key", "deepgram_api_key", "gemini_api_key"),
    "mail": ("password",),
    "bridge": ("token",),
    "telegram": ("token",),
    "slack": ("bot_token", "app_token"),
    "mailbox": ("password",),
    "matterbridge": ("slack_token", "discord_token", "telegram_token", "matrix_password"),
    "telemetry": ("headers", "prometheus_token"),
    "analytics": ("api_secret",),
    "fedora": ("password",),
    "tunnel": ("token", "api_token"),
    "https": ("dns_credentials", "eab_hmac_key"),
    "documents": ("anytopdf_token",),
    "components": ("hf_token",),
}
ENUMS = {
    ("transcribe", "engine"): {"sensevoice", "whisper", "mlx-whisper", "openai", "elevenlabs", "assemblyai", "deepgram"},
    ("transcribe", "device"): {"auto", "cpu", "cuda", "mps"},
    ("diarize", "engine"): {"auto", "channels", "cluster", "pyannote", "provider", "none"},
    ("speakers", "embedder"): {"speechbrain", "none"},
    ("speakers", "cross_namespace"): {"suggest", "off"},
    ("analysis", "entities"): {"rules", "spacy"},
    ("search", "stemming"): {"english", "none"},
    ("search", "engine"): set(store.SEARCH_ENGINES),
    ("reports", "audio"): {"link", "embed", "none"},
    ("video", "ocr_engine"): {"auto", "tesseract", "apple-vision", "rapidocr", "doctr", "none"},
    ("video", "face_engine"): {"opencv", "insightface", "anytopdf", "none"},
    ("video", "object_engine"): {"yolox", "ultralytics", "anytopdf", "off"},
    ("decisions", "engine"): {"auto", "jev", "laya", "llm", "off"},
    ("voice", "input"): {"auto", "server", "browser"},
    ("voice", "stt"): {"same", "sensevoice", "whisper", "mlx-whisper", "openai", "elevenlabs", "assemblyai", "deepgram"},
    ("voice", "tts_provider"): {"openai", "elevenlabs", "deepgram", "gemini", "say", "styletts2"},
    ("mail", "security"): {"starttls", "ssl", "none"},
    ("bridge", "answer"): {"mention", "all"},
    ("bridge", "approve"): {"off", "low_risk"},
    ("telegram", "answer"): {"mention", "all"},
    ("telegram", "approve"): {"off", "low_risk"},
    ("mailbox", "imap_security"): set(MAIL_SECURITY),
    ("mailbox", "smtp_security"): set(MAIL_SECURITY),
    ("mailbox", "approve"): {"off", "low_risk"},
    ("mailbox", "check"): {"authenticated", "off"},
    ("mailbox", "imports"): {"auto", "ask"},
    ("ai", "injection_check"): {"patterns", "classifier", "model", "off"},
    ("slack", "answer"): {"mention", "all"},
    ("slack", "approve"): {"off", "low_risk"},
    ("tunnel", "mode"): {"off", "quick", "token", "managed"},
    ("https", "ca"): {"letsencrypt", "zerossl", "both", "custom"},
    ("https", "challenge"): {"http", "tls-alpn", "dns"},
    ("https", "client_auth"): {"off", "optional", "required"},
    ("https", "min_tls"): {"1.2", "1.3"},
    ("files", "store"): {"local", "connection"},
    ("documents", "converter"): {"auto", "anytopdf", "lens"},
    ("analytics", "region"): {"global", "eu"},
    ("logs", "level"): set(logs.LEVELS),
    ("logs", "syslog_level"): set(logs.LEVELS),
    ("logs", "format"): {"text", "json"},
    ("logs", "syslog_format"): {"text", "json"},
    ("logs", "syslog_transport"): set(logs.TRANSPORTS),
    ("logs", "syslog_facility"): set(logs.FACILITIES),
    ("logs", "syslog_framing"): {"octet", "newline"},
}
# Settings the environment (.env) sets, which win over archive.yaml and the app and show as locked there: the
# break-glass allowed hosts, the model provider so an install can be configured without the setup wizard, and
# telemetry (LENS_TELEMETRY=off keeps it off whatever the app says).
ENV_OVERRIDES = {
    ("server", "allowed_hosts"): "ARCHIVE_ALLOWED_HOSTS",
    ("llm", "base_url"): "LENS_LLM_BASE_URL",
    ("llm", "model"): "LENS_LLM_MODEL",
    ("llm", "api_key"): "LENS_LLM_API_KEY",
    ("llm", "vision_model"): "LENS_LLM_VISION_MODEL",
    ("embeddings", "base_url"): "LENS_EMBED_BASE_URL",
    ("embeddings", "model"): "LENS_EMBED_MODEL",
    ("embeddings", "api_key"): "LENS_EMBED_API_KEY",
    ("decisions", "api_key"): "TYPESAFE_API_KEY",
    ("mail", "server"): "MAIL_SERVER",
    ("mail", "port"): "MAIL_PORT",
    ("mail", "username"): "MAIL_USERNAME",
    ("mail", "password"): "MAIL_PASSWORD",
    ("mail", "from_address"): "MAIL_FROM",
    ("mail", "from_name"): "MAIL_FROM_NAME",
    ("telemetry", "enabled"): "LENS_TELEMETRY",
    ("telemetry", "endpoint"): "LENS_TELEMETRY_ENDPOINT",
    ("telemetry", "headers"): "LENS_TELEMETRY_HEADERS",
    ("logs", "level"): "LENS_LOG_LEVEL",
    ("logs", "format"): "LENS_LOG_FORMAT",
    ("telemetry", "prometheus"): "LENS_PROMETHEUS",
    ("telemetry", "prometheus_token"): "LENS_PROMETHEUS_TOKEN",
    ("analytics", "enabled"): "LENS_ANALYTICS",
    ("analytics", "measurement_id"): "LENS_GA_MEASUREMENT_ID",
    ("analytics", "api_secret"): "LENS_GA_API_SECRET",
    ("telemetry", "grafana_url"): "LENS_GRAFANA_URL",
    ("fedora", "enabled"): "LENS_FEDORA",
    ("fedora", "url"): "LENS_FEDORA_URL",
    ("fedora", "user"): "LENS_FEDORA_USER",
    ("fedora", "password"): "LENS_FEDORA_PASSWORD",
    ("components", "hf_token"): "HF_TOKEN",
}


def env_value(section, key):
    """The environment's value for a setting in ENV_OVERRIDES, parsed; None when it isn't set."""
    raw = os.environ.get(ENV_OVERRIDES.get((section, key)) or "", "").strip()
    if not raw:
        return None
    default = store.DEFAULTS.get(section, {}).get(key)
    if isinstance(default, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "on", "yes")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return None
    return raw


def locked(section):
    """The keys of a section the environment sets."""
    return [k for (s, k) in ENV_OVERRIDES if s == section and env_value(s, k) is not None]


# The types uploads.extensions may name: what the folder scans import, a few more that ffmpeg reads, and documents and
# images, and JSON.
UPLOAD_TYPES = frozenset(
    [*store.MEDIA_EXT, ".aif", ".aiff", ".wma", ".mpg", ".mpeg", ".3gp", *store.DOCUMENT_EXT, *store.IMAGE_EXT, *store.STRUCTURED_EXT]
)
UPLOAD_RANGES = {"max_mb": (1, 1_000_000), "chunk_mb": (1, 64), "expire_hours": (1, 720)}
DOCUMENT_RANGES = {
    "page_pixels": (800, 6000),
    "thumb_pixels": (120, 800),
    "ocr_below_chars": (0, 5000),
    "max_pages": (1, 50_000),
    "convert_seconds": (10, 3600),
}
TOKEN_DAYS = (1, 3650)
OAUTH_ACCESS_MINUTES = (5, 1440)
VIEWER_URL = re.compile(r"^https?://[^\s]+$")
_KEYS, _KL = {}, threading.Lock()


def secret_key(cfg):
    """ARCHIVE_SECRET_KEY if set, else a random key created once in data_dir/secret.key (mode 600)."""
    env = os.environ.get("ARCHIVE_SECRET_KEY")
    if env:
        return hashlib.sha256(env.encode()).digest()
    p = pathlib.Path(cfg["data_dir"]) / "secret.key"
    with _KL:
        if str(p) not in _KEYS:
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as f:
                        f.write(secrets.token_urlsafe(48))
                except FileExistsError:
                    pass
            _KEYS[str(p)] = hashlib.sha256(p.read_text().strip().encode()).digest()
        return _KEYS[str(p)]


def seal(cfg, value, context):
    """AES-GCM; the context (e.g. 'setting:llm.api_key') is bound in, so a ciphertext can't be moved to another field."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    return "v1." + base64.urlsafe_b64encode(nonce + AESGCM(secret_key(cfg)).encrypt(nonce, value.encode(), context.encode())).decode()


def unseal(cfg, sealed, context):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.urlsafe_b64decode(sealed[3:])
    return AESGCM(secret_key(cfg)).decrypt(raw[:12], raw[12:], context.encode()).decode()


def _data(row):
    """Saved values are kept as JSON text: SurrealDB drops fields set to NONE, and "cleared" has to survive."""
    d = row.get("data")
    return json.loads(d) if isinstance(d, str) else dict(d or {})


def effective(db, base, reveal=True):
    cfg = copy.deepcopy(base)
    for row in db.rows("SELECT record::id(id) AS section, data, sealed FROM app_setting"):
        sec = row["section"]
        if sec not in EDITABLE or not isinstance(cfg.get(sec), dict):
            continue
        allowed = EDITABLE[sec]
        cfg[sec] = store._merge(cfg[sec], {k: v for k, v in _data(row).items() if allowed is None or k in allowed})
        if sec == "documents":  # the web app adds networks to the server's own (archive.yaml, LENS_WEB_NETWORKS)
            server = (base.get("documents") or {}).get("web_networks") or []
            cfg[sec]["web_networks"] = list(dict.fromkeys([*server, *(cfg[sec].get("web_networks") or [])]))
        for k, sealed in (row.get("sealed") or {}).items():
            if not reveal:
                cfg[sec][k] = {"secret": True, "set": True}
                continue
            try:
                cfg[sec][k] = unseal(base, sealed, f"setting:{sec}.{k}")
            except Exception:  # noqa: BLE001 - key changed since it was saved
                cfg[sec][k] = None
    for sec, key in ENV_OVERRIDES:
        v = env_value(sec, key)
        if v is not None:
            cfg[sec][key] = {"secret": True, "set": True} if not reveal and key in SECRETS.get(sec, ()) else v
    return cfg


def keep_passwords(db, base):
    """An install from before passkeys: its people sign in with passwords, so they keep working (auth.passwords on)
    until an admin turns them off. Runs at startup; does nothing once auth is saved or where nobody has a password."""
    if db.one("SELECT id FROM $r", r=R("app_setting", "auth")) or (base.get("auth") or {}).get("passwords"):
        return False
    if not db.values("SELECT VALUE id FROM account WHERE pw != NONE LIMIT 1"):
        return False
    save(db, base, "auth", {"passwords": True}, "upgrade")
    return True


class Settings:
    """The effective configuration, rebuilt only when someone saves a setting."""

    def __init__(self, db, base):
        self.db, self.base, self._cfg, self._ver, self._lock = db, base, None, None, threading.Lock()

    def current(self):
        v = self.db.values("SELECT VALUE n FROM $r", r=R("seq", "settings"))
        with self._lock:
            if self._cfg is None or v != self._ver:
                self._cfg, self._ver = effective(self.db, self.base), v
                telemetry.apply(self._cfg)  # turned on, changed or off in the app: this process follows
                logs.apply(self._cfg)  # and its logging (level, file, syslog)
            return self._cfg


def view(db, base):
    eff = effective(db, base, reveal=False)
    meta = {r["section"]: r for r in db.rows("SELECT record::id(id) AS section, updated_at, updated_by FROM app_setting")}
    out = {}
    for sec, allowed in EDITABLE.items():
        vals = dict(eff.get(sec) or {})
        if allowed is not None:
            vals = {k: vals.get(k) for k in allowed}
        for k in SECRETS.get(sec, ()):
            if not isinstance(vals.get(k), dict):
                vals[k] = {"secret": True, "set": False}
        out[sec] = {
            "values": vals,
            "updated_at": (meta.get(sec) or {}).get("updated_at"),
            "updated_by": (meta.get(sec) or {}).get("updated_by"),
            "locked": locked(sec),
        }
    out["bootstrap"] = {
        "database": db.url,
        "data_dir": base["data_dir"],
        "secret_key": "ARCHIVE_SECRET_KEY" if os.environ.get("ARCHIVE_SECRET_KEY") else "data_dir/secret.key",
        "rclone": base["sources"].get("rclone") or "rclone on PATH",
        "local_roots": base["sources"].get("local_roots") or [],
        **convert.bootstrap(base),
        "yolox_model": objects.yolox_model(base) or "not found",
    }
    return out


def _updates_setting(key, value):
    if key == "check_hours":
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 24 * 30):
            raise ValueError("updates.check_hours is a whole number of hours from 1 to 720")
        return value
    if not isinstance(value, bool):
        raise ValueError(f"updates.{key} is true or false")
    return value


def _check(section, key, value, default):
    enum = ENUMS.get((section, key))
    if enum and value not in enum:
        raise ValueError(f"{section}.{key} must be one of: {', '.join(sorted(enum))}")
    if (section, key) == ("iiif", "viewers"):
        # "Open in" links: [{name, url}], where the URL may use {manifest} and {content_state}. Only http(s) URLs,
        # since they become links in the web app.
        if not (
            isinstance(value, list)
            and all(
                isinstance(v, dict)
                and set(v) <= {"name", "url"}
                and isinstance(v.get("name", ""), str)
                and isinstance(v.get("url"), str)
                and VIEWER_URL.match(v["url"])
                for v in value
            )
        ):
            raise ValueError("iiif.viewers is a list of {name, url} with an http(s) URL; the URL may use {manifest} and {content_state}")
        return value
    if (section, key) == ("server", "trusted_proxies"):
        return ipgroups.proxies(value)
    if (section, key) == ("llm", "vision_model"):
        if value is not None and not (isinstance(value, str) and len(value.strip()) <= 200):
            raise ValueError("llm.vision_model is a model's name")
        return (value or "").strip() or None
    if (section, key) == ("ai", "compact_model"):
        if value is not None and not (isinstance(value, str) and len(value.strip()) <= 200):
            raise ValueError("ai.compact_model is a model's name")
        return (value or "").strip() or None
    if (section, key) == ("ai", "context_tokens"):
        if value is not None and not (isinstance(value, int) and not isinstance(value, bool) and 1024 <= value <= 2_000_000):
            raise ValueError("ai.context_tokens is a whole number of tokens from 1024 to 2000000")
        return value
    if (section, key) == ("ai", "browser_steps"):
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 100):
            raise ValueError("ai.browser_steps is a whole number of steps from 1 to 100")
        return value
    if (section, key) == ("llm", "describe_max"):
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 1000):
            raise ValueError("llm.describe_max is a whole number from 1 to 1000")
        return value
    if (section, key) == ("llm", "chat_models"):
        names = [v.strip() for v in value] if isinstance(value, list) and all(isinstance(v, str) for v in value) else None
        if names is None or not all(names) or len(names) > 50 or any(len(n) > 200 for n in names):
            raise ValueError("llm.chat_models is a list of up to 50 model names")
        return list(dict.fromkeys(names))
    if (section, key) == ("encryption", "work_minutes"):
        if not (isinstance(value, int) and not isinstance(value, bool) and 5 <= value <= 1440):
            raise ValueError("encryption.work_minutes is a whole number of minutes from 5 to 1440")
        return value
    if (section, key) == ("encryption", "vault_minutes"):
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 1440):
            raise ValueError("encryption.vault_minutes is a whole number of minutes from 1 to 1440")
        return value
    if section == "embeddings":
        return _embed_setting(key, value)
    if section == "uploads":
        return _upload_setting(key, value)
    if section == "notifications":
        return _notify_setting(key, value)
    if section == "telemetry":
        return _telemetry_setting(key, value)
    if section == "analytics" and key != "region":
        return _analytics_setting(key, value)
    if section == "logs":
        return _logs_setting(key, value)
    if section == "updates":
        return _updates_setting(key, value)
    if section == "activity":
        return _activity_setting(key, value)
    if section == "events":
        return _events_setting(key, value)
    if section == "automations":
        return _automations_setting(key, value)
    if section == "backups":
        return _backup_setting(key, value)
    if section == "fedora":
        return _fedora_setting(key, value)
    if section == "shares":
        return _share_setting(key, value)
    if section == "sensors":
        return _sensor_setting(key, value)
    if section == "nfs":
        return _nfs_setting(key, value)
    if section == "smb":
        return _smb_setting(key, value)
    if section == "components":
        return _component_setting(key, value)
    if section == "nsfw":
        return _nsfw_setting(key, value)
    if section == "mail" and key != "security":
        return _mail_setting(key, value)
    if section == "bridge" and key not in ("answer", "enabled", "approve"):
        return _bridge_setting(key, value)
    if section == "telegram" and key not in ("answer", "enabled", "approve"):
        return _telegram_setting(key, value)
    if section == "slack" and key not in ("answer", "enabled", "approve"):
        return _slack_setting(key, value)
    if section == "mailbox" and key not in ("enabled", "approve", "imap_security", "smtp_security", "check", "imports"):
        return _mailbox_setting(key, value)
    if section == "matterbridge" and key != "run":
        return _matterbridge_setting(key, value)
    if (section, key) == ("voice", "tts_base_url"):
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("voice.tts_base_url is the http(s) address of an OpenAI-compatible server with /audio/speech")
        return value.strip().rstrip("/")
    if (section, key) == ("voice", "styletts2_url"):
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("voice.styletts2_url is the http(s) address of the styletts2 service")
        return value.strip().rstrip("/")
    if section == "voice" and key in ("tts_model", "tts_voice", "tts_api_key"):
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and len(value.strip()) <= 500):
            raise ValueError(f"voice.{key} is text")
        return value.strip()
    if section == "speech":
        return _speech_setting(key, value)
    if section == "podcasts":
        return _podcast_setting(key, value)
    if section == "local_llm":
        return _local_llm_setting(key, value)
    if section == "decisions" and key != "engine":
        return _decision_setting(key, value)
    if (section, key) in (
        ("documents", "attachment_resources"),
        ("documents", "video_urls"),
        ("documents", "tika_text"),
        ("documents", "tika_metadata"),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"documents.{key} is true or false")
        return value
    if (section, key) == ("documents", "converter"):
        return value
    if (section, key) == ("documents", "anytopdf_url"):
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("documents.anytopdf_url is a conversion node's http(s) address, such as https://convert.home:8640")
        return value.strip().rstrip("/")
    if (section, key) == ("documents", "tika_url"):
        if value in (None, ""):
            return None
        u = urllib.parse.urlsplit(value.strip()) if isinstance(value, str) else None
        if not (u and VIEWER_URL.match(value.strip()) and u.hostname and u.username is None and u.password is None):
            raise ValueError("documents.tika_url is a Tika server's http(s) address with no password in it, such as http://tika:9998")
        return value.strip().rstrip("/")
    if (section, key) == ("documents", "web_networks"):
        return _web_networks(value)
    if (section, key) == ("documents", "anytopdf_token"):
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and 16 <= len(value.strip()) <= 500):
            raise ValueError("documents.anytopdf_token is the conversion node's token (ANYTOPDF_QUEUE_TOKEN), 16 characters or more")
        return value.strip()
    if section == "documents":
        lo, hi = DOCUMENT_RANGES[key]
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"documents.{key} is a whole number from {lo} to {hi}")
        return value
    if section == "files" and key == "connection":
        if value is not None and not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            raise ValueError("files.connection is a storage connection's id")
        return value
    if (section, key) == ("files", "folder"):
        from . import blobs

        if not isinstance(value, str) or len(value) > 200:
            raise ValueError("files.folder is a folder (a bucket and path for S3), up to 200 characters")
        return blobs._folder(value)
    if (section, key) == ("video", "object_min_score"):
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0.05 <= value <= 0.95):
            raise ValueError("video.object_min_score is a number from 0.05 to 0.95")
        return float(value)
    if (section, key) == ("tokens", "oauth_access_minutes"):
        lo, hi = OAUTH_ACCESS_MINUTES
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"tokens.oauth_access_minutes is a whole number of minutes from {lo} to {hi}")
        return value
    if (section, key) == ("tokens", "oauth_enabled"):
        if not isinstance(value, bool):
            raise ValueError("tokens.oauth_enabled is true or false")
        return value
    if section == "tokens" and key != "never_expire":
        lo, hi = TOKEN_DAYS
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"tokens.{key} is a whole number of days from {lo} to {hi}")
        return value
    if default is None or value is None:
        return value
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, (int, float)):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif isinstance(default, str):
        ok = isinstance(value, str)
    elif isinstance(default, list):
        ok = isinstance(value, list) and all(isinstance(x, (str, int, float)) for x in value)
    elif isinstance(default, dict):
        ok = isinstance(value, dict)
        extra = sorted(set(value) - set(default)) if ok and default else []
        if extra:
            raise ValueError(f"unknown setting {section}.{key}.{extra[0]}")
    else:
        ok = True
    if not ok:
        raise ValueError(f"{section}.{key} should be {type(default).__name__}")
    return value


# What the web app can't open web pages, feeds and downloads to: this machine (loopback), and link-local addresses,
# where clouds answer with the server's own credentials (169.254.169.254). archive.yaml or LENS_WEB_NETWORKS, on the
# server, can still list them.
SERVER_ONLY_NETWORKS = [ipaddress.ip_network(n) for n in ("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "::/127", "fe80::/10")]


def _network(value):
    try:
        return str(ipaddress.ip_network(str(value).strip(), strict=False))
    except ValueError:
        return str(value)


def _web_networks(value):
    """documents.web_networks from the web app: private networks pages and feeds may be fetched from."""
    if not isinstance(value, list):
        raise ValueError("documents.web_networks is a list of networks like 192.168.1.0/24")
    out = []
    for v in value:
        try:
            net = ipaddress.ip_network(str(v).strip(), strict=False)
        except ValueError:
            raise ValueError(f"documents.web_networks: {v} isn't a network like 192.168.1.0/24") from None
        if any(net.version == bad.version and net.overlaps(bad) for bad in SERVER_ONLY_NETWORKS):
            raise ValueError(
                f"documents.web_networks: {net} reaches this server itself or a cloud's metadata address, which only "
                "archive.yaml or LENS_WEB_NETWORKS on the server can allow"
            )
        out.append(str(net))
    return list(dict.fromkeys(out))


LOCAL_LLM_RANGES = {"context": (512, 262144), "threads": (1, 512), "gpu_layers": (0, 999), "port": (1024, 65535)}


def _local_llm_setting(key, value):
    from . import local_llm

    if key in ("enabled", "use_as_provider"):
        if not isinstance(value, bool):
            raise ValueError(f"local_llm.{key} is true or false")
        return value
    if key == "model":
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError("local_llm.model is a model from the list, or hf:<owner>/<repo>/<file>.gguf")
        try:
            local_llm.resolve(value.strip())
        except local_llm.LocalModelError as e:
            raise ValueError(f"local_llm.model: {e}") from None
        return value.strip()
    if key == "host":
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9.:\[\]-]{1,253}", value.strip())):
            raise ValueError("local_llm.host is a host name or address other processes reach this server at")
        return value.strip()
    if key == "threads" and value is None:
        return None
    lo, hi = LOCAL_LLM_RANGES[key]
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"local_llm.{key} is a whole number from {lo} to {hi}")
    return value


def _speech_setting(key, value):
    if key.endswith("_base_url"):
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError(f"speech.{key} is an http(s) address (the provider's, a proxy's or a compatible server's)")
        return value.strip().rstrip("/")
    if key.endswith("_model"):
        if not (isinstance(value, str) and value.strip() and len(value.strip()) <= 200):
            raise ValueError(f"speech.{key} is a model's name")
        return value.strip()
    if key == "sentiment":
        if not isinstance(value, bool):
            raise ValueError("speech.sentiment is true or false")
        return value
    if key == "timeout":
        if not (isinstance(value, int) and not isinstance(value, bool) and 30 <= value <= 86400):
            raise ValueError("speech.timeout is a whole number of seconds from 30 to 86400")
        return value
    for end, (lo, hi), what in (("_max_minutes", SPEECH_MINUTES, "minutes"), ("_max_mb", SPEECH_MB, "MB")):
        if key.endswith(end):
            if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
                raise ValueError(f"speech.{key} is a whole number of {what} from {lo} to {hi}")
            return value
    raise ValueError(f"unknown setting speech.{key}")


SPEECH_MINUTES = (1, 1440)  # the longest piece of a recording sent to a speech provider at once
SPEECH_MB = (1, 10_000)  # its largest file


PODCAST_RANGES = {"context_chars": (2000, 400_000), "max_minutes": (1, 120), "words_per_minute": (80, 250)}


def _podcast_setting(key, value):
    if key in PODCAST_RANGES:
        lo, hi = PODCAST_RANGES[key]
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"podcasts.{key} is a whole number from {lo} to {hi}")
        return value
    if key == "namespace":
        if not (isinstance(value, str) and store.NS_RX.match(value.strip())):
            raise ValueError("podcasts.namespace is a namespace name (lowercase letters, digits, - and _)")
        return value.strip()
    if key in ("host_a", "host_b"):
        if not (isinstance(value, str) and 0 < len(value.strip()) <= 40):
            raise ValueError(f"podcasts.{key} is a name of up to 40 characters")
        return value.strip()
    if key in ("model", "voice_a", "voice_b"):
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and len(value.strip()) <= 200):
            raise ValueError(f"podcasts.{key} is a name")
        return value.strip()
    raise ValueError(f"unknown setting podcasts.{key}")


EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _mail_setting(key, value):
    if key == "port":
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535):
            raise ValueError("mail.port is a port number, usually 587 (STARTTLS), 465 (SSL) or 25")
        return value
    if value in (None, "") and key != "from_name":
        return None
    if not (isinstance(value, str) and len(value.strip()) <= 300):
        raise ValueError(f"mail.{key} is text")
    value = value.strip() if key != "password" else value
    if key == "server" and not re.match(r"^[A-Za-z0-9.\-\[\]:]+$", value):
        raise ValueError("mail.server is the SMTP server's host name, such as smtp.gmail.com")
    if key == "from_address" and not EMAIL_RX.match(value):
        raise ValueError("mail.from_address is an email address")
    if key == "from_name" and not value:
        return "Lens"
    if key not in ("server", "port", "username", "password", "from_address", "from_name"):
        raise ValueError(f"unknown setting mail.{key}")
    return value


TELEGRAM_TOKEN_RX = re.compile(r"^[0-9]{3,20}:[A-Za-z0-9_-]{30,64}$")


def _bridge_setting(key, value, section="bridge"):
    """bridge's settings, and the room settings every chat channel shares (channels.py: `section`)."""
    if key == "poll_seconds":
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 300):
            raise ValueError(f"{section}.poll_seconds is a whole number of seconds from 1 to 300")
        return value
    if key == "users":
        if not (isinstance(value, list) and all(isinstance(v, str) and len(v) <= 200 for v in value) and len(value) <= 500):
            raise ValueError(f"{section}.users is a list of chat usernames")
        return list(dict.fromkeys(v.strip().lstrip("@") if section != "bridge" else v.strip() for v in value if v.strip()))
    if key == "rooms":
        bad = f"{section}.rooms is a list of rooms and namespaces, like team = pods or team/general = pods"
        if not (isinstance(value, list) and len(value) <= 200 and all(isinstance(v, str) and len(v) <= 300 for v in value)):
            raise ValueError(bad)
        out = []
        for v in value:
            if not v.strip():
                continue
            where, eq, ns = v.partition("=")
            where, ns = where.strip(), ns.strip().lower()
            if not eq or not where or not store.NS_RX.match(ns):
                raise ValueError(bad)
            out.append(f"{where} = {ns}")
        return list(dict.fromkeys(out))
    if key not in ("url", "token", "gateway", "account", "name"):
        raise ValueError(f"unknown setting {section}.{key}")
    if value in (None, ""):
        return "Lens" if key == "name" else None
    if not (isinstance(value, str) and len(value.strip()) <= 500):
        raise ValueError(f"{section}.{key} is text")
    value = value.strip() if key != "token" else value
    if key == "url":
        u = urllib.parse.urlsplit(value)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("bridge.url is the address of Matterbridge's API, such as http://matterbridge:4242")
        return value.rstrip("/")
    if key == "account":
        if not EMAIL_RX.match(value):
            raise ValueError(f"{section}.account is the email of the Lens account it answers as")
        return value.lower()
    if key == "name" and not re.match(r"^[\w .-]{1,40}$", value):
        raise ValueError(f"{section}.name is a short name, such as Lens")
    return value


def _telegram_setting(key, value):
    if key == "token":
        value = (value or "").strip()
        if value and not TELEGRAM_TOKEN_RX.match(value):
            raise ValueError("telegram.token is the bot's token from @BotFather, like 123456789:AAE…")
        return value or None
    if key == "api_url":
        if value in (None, ""):
            return None
        u = urllib.parse.urlsplit(value.strip()) if isinstance(value, str) else None
        if not u or u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("telegram.api_url is the http(s) address of a Bot API server, such as https://api.telegram.org")
        return value.strip().rstrip("/")
    if key in ("url", "gateway", "poll_seconds"):  # Matterbridge's own; Telegram waits on getUpdates instead
        raise ValueError(f"unknown setting telegram.{key}")
    return _bridge_setting(key, value, "telegram")


def _slack_setting(key, value):
    prefix = {"bot_token": "xoxb-", "app_token": "xapp-"}.get(key)
    if prefix:
        value = (value or "").strip() if isinstance(value, str) or value is None else value
        if value and not (isinstance(value, str) and value.startswith(prefix) and len(value) <= 500 and " " not in value):
            raise ValueError(f"slack.{key} is the Slack app's token starting {prefix}")
        return value or None
    if key in ("url", "token", "gateway", "poll_seconds"):  # Matterbridge's own; Slack keeps a socket open instead
        raise ValueError(f"unknown setting slack.{key}")
    return _bridge_setting(key, value, "slack")


HOST_RX = re.compile(r"^[A-Za-z0-9.\-\[\]:]{1,253}$")


def _mailbox_setting(key, value):
    """The assistant's mailbox (mailbox.py), and the room settings every chat channel shares."""
    if key in ("imap_port", "smtp_port"):
        if value in (None, ""):
            return None
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535):
            raise ValueError(f"mailbox.{key} is a port number, such as 993 for IMAP or 587 for SMTP")
        return value
    if key == "poll_seconds":
        if not (isinstance(value, int) and not isinstance(value, bool) and 10 <= value <= 3600):
            raise ValueError("mailbox.poll_seconds is a whole number of seconds from 10 to 3600")
        return value
    if key == "users":
        if not (isinstance(value, list) and len(value) <= 500 and all(isinstance(v, str) and len(v) <= 300 for v in value)):
            raise ValueError("mailbox.users is a list of email addresses")
        out = [v.strip().strip("<>").lower() for v in value if v.strip()]
        bad = next((v for v in out if not EMAIL_RX.match(v)), None)
        if bad:
            raise ValueError(f"mailbox.users is a list of email addresses: {bad} isn't one")
        return list(dict.fromkeys(out))
    if key == "routes":
        if not (
            isinstance(value, list) and len(value) <= mail_routes.MAX_RULES and all(isinstance(v, str) and len(v) <= 2000 for v in value)
        ):
            raise ValueError(f"mailbox.routes is a list of up to {mail_routes.MAX_RULES} rules, like from: *@acme.com = acme")
        out = []
        for v in value:
            if not v.strip():
                continue
            rule = mail_routes.parse_line(v)
            if not rule.get("skip") and not store.NS_RX.match(rule["namespace"]):
                raise ValueError(f"mailbox.routes: {rule['namespace']} isn't a namespace's name")
            out.append(mail_routes.line(rule))
        return list(dict.fromkeys(out))
    if key in ("account", "name"):
        return _bridge_setting(key, value, "mailbox")
    if key not in ("address", "username", "imap_host", "smtp_host", "folder", "auth_server"):
        raise ValueError(f"unknown setting mailbox.{key}")
    if value in (None, ""):
        return "INBOX" if key == "folder" else None
    if not (isinstance(value, str) and len(value.strip()) <= 300):
        raise ValueError(f"mailbox.{key} is text")
    value = value.strip()
    if key == "address":
        if not EMAIL_RX.match(value):
            raise ValueError("mailbox.address is the assistant's email address, such as lens@example.org")
        return value.lower()
    if key in ("imap_host", "smtp_host") and not HOST_RX.match(value):
        raise ValueError(f"mailbox.{key} is the mail server's host name, such as {key[:4]}.example.org")
    if key == "auth_server":
        if not re.match(r"^[A-Za-z0-9.\-]{1,253}$", value):
            raise ValueError("mailbox.auth_server is the name your mail server signs Authentication-Results with, such as mx.google.com")
        return value.lower()
    if key == "folder" and ("\n" in value or "\r" in value):
        raise ValueError("mailbox.folder is a folder's name, such as INBOX")
    return value


def _matterbridge_setting(key, value):
    if key.endswith(("_channels", "_chats", "_rooms", "_groups")):
        if not (isinstance(value, list) and len(value) <= 100 and all(isinstance(v, str) and len(v) <= 200 for v in value)):
            raise ValueError(f"matterbridge.{key} is a list of rooms, one per line")
        return list(dict.fromkeys(v.strip() for v in value if v.strip()))
    if key not in store.DEFAULTS["matterbridge"]:
        raise ValueError(f"unknown setting matterbridge.{key}")
    if value in (None, ""):
        return None
    if not (isinstance(value, str) and len(value.strip()) <= 500) or "\n" in value:
        raise ValueError(f"matterbridge.{key} is one line of text")
    value = value.strip()
    if key == "matrix_server":
        u = urllib.parse.urlsplit(value)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("matterbridge.matrix_server is the https:// address of the Matrix homeserver")
    if key == "whatsapp_number" and not re.match(r"^\+[0-9]{6,16}$", value.replace(" ", "")):
        raise ValueError("matterbridge.whatsapp_number is the phone number with its country code, like +441234567890")
    return value.replace(" ", "") if key == "whatsapp_number" else value


def _component_setting(key, value):
    from . import components

    if key in ("auto", "ahead"):
        if not isinstance(value, bool):
            raise ValueError(f"components.{key} is true or false")
        return value
    if key == "also":
        known = {c.id for c in components.COMPONENTS if c.kind != "program"}
        if not (isinstance(value, list) and all(isinstance(v, str) and v in known for v in value)):
            raise ValueError("components.also lists components to fetch: " + ", ".join(sorted(known)))
        return list(dict.fromkeys(value))
    if key == "hf_token":
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_\-]{8,500}", value.strip())):
            raise ValueError("components.hf_token is a Hugging Face access token, such as hf_…")
        return value.strip()
    raise ValueError(f"unknown setting components.{key}")


def _nsfw_setting(key, value):
    from . import nsfw

    if key == "enabled":
        if not isinstance(value, bool):
            raise ValueError("nsfw.enabled is true or false")
        return value
    if key == "threshold":
        return nsfw.check_threshold(value, "nsfw.threshold")
    if key == "display":
        return nsfw.check_display(value, "nsfw.display")
    if key == "max_frames":
        lo, hi = nsfw.MAX_FRAMES
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"nsfw.max_frames is a whole number from {lo} to {hi}")
        return value
    raise ValueError(f"unknown setting nsfw.{key}")


def _decision_setting(key, value):
    if key == "laya_model":
        from . import decide

        if value not in decide.LAYA_MODELS:
            raise ValueError("decisions.laya_model is one of " + ", ".join(decide.LAYA_MODELS))
        return value
    if key == "laya_url":
        if value is None or value == "":
            return None
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("decisions.laya_url is a Laya server's http(s) address, such as http://host.docker.internal:8790/v1")
        return value.strip().rstrip("/")
    if key == "base_url":
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("decisions.base_url is the decision model's http(s) address, such as https://api.typesafe.ai/v1")
        return value.strip().rstrip("/")
    if key in ("model", "api_key"):
        if value is None or value == "":
            if key == "model":
                raise ValueError("decisions.model names the decision model, such as jev-latest")
            return None
        if not (isinstance(value, str) and len(value.strip()) <= 500):
            raise ValueError(f"decisions.{key} is text")
        return value.strip()
    if key == "act_above":
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0.5 <= value <= 1):
            raise ValueError("decisions.act_above is a number from 0.5 to 1")
        return float(value)
    if key == "timeout":
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= value <= 120):
            raise ValueError("decisions.timeout is a number of seconds from 1 to 120")
        return value
    if key == "price_per_call":
        if value is None or value == "":
            return None
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100):
            raise ValueError("decisions.price_per_call is what one decision costs, in USD, from 0 to 100")
        return float(value)
    raise ValueError(f"unknown setting decisions.{key}")


def _activity_setting(key, value):
    if key in ("enabled", "reads"):
        if not isinstance(value, bool):
            raise ValueError(f"activity.{key} is true or false")
        return value
    if key == "keep_days":
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3650):
            raise ValueError("activity.keep_days is a whole number of days from 1 to 3650")
        return value
    raise ValueError(f"unknown setting activity.{key}")


def _events_setting(key, value):
    """The stream of activities automations follow (docs/automations.md)."""
    limits = {"keep_days": (1, 3650, "a whole number of days"), "poll_seconds": (1, 3600, "a whole number of seconds")}
    if key not in limits:
        raise ValueError(f"unknown setting events.{key}")
    lo, hi, what = limits[key]
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"events.{key} is {what} from {lo} to {hi}")
    return value


def _automations_setting(key, value):
    """Automations (docs/automations.md): how long an automation's action waits for the person's pick."""
    limits = {"approval_days": (1, 365, "a whole number of days")}
    if key not in limits:
        raise ValueError(f"unknown setting automations.{key}")
    lo, hi, what = limits[key]
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"automations.{key} is {what} from {lo} to {hi}")
    return value


def _backup_setting(key, value):
    from . import schedule

    if key == "schedule":
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError("backups.schedule is a cron expression, like 0 3 * * * (every night at 3), or empty for none")
        try:
            schedule.parse(value.strip())
        except ValueError as e:
            raise ValueError(f"backups.schedule: {e}") from None
        return value.strip()
    if key == "timezone":
        if not isinstance(value, str):
            raise ValueError("backups.timezone is a time zone name, like Europe/London")
        try:
            schedule.zone(value)
        except ValueError as e:
            raise ValueError(f"backups.timezone: {e}") from None
        return value
    if key == "keep":
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 365):
            raise ValueError("backups.keep is a whole number of scheduled backups from 1 to 365")
        return value
    if key == "connection":
        if value is not None and not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            raise ValueError("backups.connection is a storage connection's id, or none")
        return value
    if key == "folder":
        from . import blobs

        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("backups.folder is a folder (a bucket and path for S3), up to 200 characters")
        return blobs._folder(value)
    raise ValueError(f"unknown setting backups.{key}")


EMBED_RANGES = {"passage_chars": (200, 4000), "batch_size": (1, 256), "neighbours": (5, 500), "timeout": (5, 600)}


def _embed_setting(key, value):
    if key == "enabled":
        if not isinstance(value, bool):
            raise ValueError("embeddings.enabled is true or false")
        return value
    if key == "base_url":
        if value is None or value == "":
            return None
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("embeddings.base_url is the http(s) address of an OpenAI-compatible server, such as http://localhost:11434/v1")
        return value.strip().rstrip("/")
    if key in ("model", "api_key_env"):
        if value is None or value == "":
            if key == "model":
                raise ValueError("embeddings.model names the embedding model, such as nomic-embed-text")
            return None
        if not (isinstance(value, str) and len(value.strip()) <= 200):
            raise ValueError(f"embeddings.{key} is a name")
        return value.strip()
    if key in ("query_prefix", "document_prefix"):
        if value is not None and not (isinstance(value, str) and len(value) <= 200):
            raise ValueError(f"embeddings.{key} is text of up to 200 characters, or none for what suits the model")
        return value
    if key == "min_similarity":
        if value is None:
            return None
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1):
            raise ValueError("embeddings.min_similarity is a number from 0 to 1, or none for what suits the model")
        return float(value)
    lo, hi = EMBED_RANGES[key]
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"embeddings.{key} is a whole number from {lo} to {hi}")
    return value


NOTIFY_RANGES = {"poll_seconds": (1, 3600), "max_attempts": (1, 20)}


def _notify_setting(key, value):
    if key == "enabled":
        if not isinstance(value, bool):
            raise ValueError("notifications.enabled is true or false")
        return value
    if key == "networks":
        if not isinstance(value, list):
            raise ValueError("notifications.networks is a list of networks like 192.168.1.0/24")
        out = []
        for v in value:
            try:
                out.append(str(ipaddress.ip_network(str(v).strip(), strict=False)))
            except ValueError:
                raise ValueError(f"notifications.networks: {v} isn't a network like 192.168.1.0/24") from None
        return list(dict.fromkeys(out))
    if key == "app_url":
        if value is None or value == "":
            return None
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("notifications.app_url is the web app's http(s) address")
        return value.strip().rstrip("/")
    lo, hi = NOTIFY_RANGES[key]
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"notifications.{key} is a whole number from {lo} to {hi}")
    return value


SENSOR_RANGES = {
    "mqtt_port": (1, 65535),
    "syslog_port": (1, 65535),
    "max_payload_kb": (1, 16384),
    "raw_days": (1, 36500),
    "rollup_days": (1, 36500),
    "important_days": (1, 36500),
    "max_per_minute": (1, 100_000),
}


def _sensor_setting(key, value):
    if key in ("enabled", "mqtt", "mqtt_anonymous", "syslog", "triage"):
        if not isinstance(value, bool):
            raise ValueError(f"sensors.{key} is true or false")
        return value
    if key == "store":
        if value not in ("all", "changes", "summary", "none"):
            raise ValueError("sensors.store is one of: all, changes, summary, none")
        return value
    if key == "syslog_networks":
        if not isinstance(value, list):
            raise ValueError("sensors.syslog_networks is a list of networks like 192.168.1.0/24")
        out = []
        for v in value:
            try:
                out.append(str(ipaddress.ip_network(str(v).strip(), strict=False)))
            except ValueError:
                raise ValueError(f"sensors.syslog_networks: {v} isn't a network like 192.168.1.0/24") from None
        return list(dict.fromkeys(out))
    lo, hi = SENSOR_RANGES[key]
    if key in ("raw_days", "rollup_days", "important_days") and value is None:
        return None  # kept for good
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"sensors.{key} is a whole number from {lo} to {hi}")
    return value


LOG_RANGES = {"file_mb": (1, 100), "file_keep": (1, 10), "syslog_port": (1, 65535)}
APP_NAME_RX = re.compile(r"^[!-~]{1,48}$")
HOST_RX = re.compile(r"^[A-Za-z0-9.:_-]{1,253}$")


def _logs_setting(key, value):
    if ("logs", key) in ENUMS:  # checked against ENUMS already
        return value
    if key in ("file", "syslog", "syslog_verify"):
        if not isinstance(value, bool):
            raise ValueError(f"logs.{key} is true or false")
        return value
    if key == "levels":
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > 100:
            raise ValueError('logs.levels gives loggers their own level, like {"httpx": "warning"}')
        out = {}
        for name, lvl in value.items():
            if not logs.LOGGER_RX.match(str(name)) or str(lvl).lower() not in logs.LEVELS:
                raise ValueError(f"logs.levels: {name} needs a logger name and one of: {', '.join(logs.LEVELS)}")
            out[str(name)] = str(lvl).lower()
        return out
    if key == "syslog_host":
        if value in (None, ""):
            return None
        v = str(value).strip().strip("[]")
        if not HOST_RX.match(v):
            raise ValueError("logs.syslog_host is the syslog server's host name or IP address, without a port")
        try:
            link_local = ipaddress.ip_address(v).is_link_local
        except ValueError:
            if ":" in v:  # only an IPv6 address has colons; the port is logs.syslog_port
                raise ValueError("logs.syslog_host is the syslog server's host name or IP address, without a port") from None
            link_local = False
        if link_local:
            raise ValueError("logs.syslog_host can't be a link-local address (169.254.0.0/16, fe80::/10)")
        return v
    if key == "syslog_app_name":
        if not (isinstance(value, str) and APP_NAME_RX.match(value.strip())):
            raise ValueError("logs.syslog_app_name is up to 48 printable characters, without spaces")
        return value.strip()
    lo, hi = LOG_RANGES[key]
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"logs.{key} is a whole number from {lo} to {hi}")
    return value


def _nfs_setting(key, value):
    if key == "enabled":
        if not isinstance(value, bool):
            raise ValueError("nfs.enabled is true or false")
        return value
    if key == "port":
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535):
            raise ValueError("nfs.port is a whole number from 1 to 65535")
        return value
    if key == "account":
        if value in (None, ""):
            return None
        if not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            raise ValueError("nfs.account is an account's id, or empty for the first admin")
        return value
    if not isinstance(value, list) or not value:
        raise ValueError("nfs.allowed_networks is a list of networks like 192.168.1.0/24 (at least one)")
    out = []
    for v in value:
        try:
            net = ipaddress.ip_network(str(v).strip(), strict=False)
        except ValueError:
            raise ValueError(f"nfs.allowed_networks: {v} isn't a network like 192.168.1.0/24") from None
        if net.prefixlen == 0:  # NFS has no sign-in: the whole internet is never a sensible default to type
            raise ValueError(f"nfs.allowed_networks: {v} is every address; list the networks your machines are on")
        out.append(str(net))
    return list(dict.fromkeys(out))


def _smb_setting(key, value):
    if key == "enabled":
        if not isinstance(value, bool):
            raise ValueError("smb.enabled is true or false")
        return value
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("smb.allowed_networks is a list of networks like 192.168.1.0/24")
    out = []
    for v in value:
        try:
            out.append(str(ipaddress.ip_network(str(v).strip(), strict=False)))
        except ValueError:
            raise ValueError(f"smb.allowed_networks: {v} isn't a network like 192.168.1.0/24") from None
    return list(dict.fromkeys(out))


def _share_setting(key, value):
    if key in ("enabled", "s3", "webdav"):
        if not isinstance(value, bool):
            raise ValueError(f"shares.{key} is true or false")
        return value
    if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535):
        raise ValueError(f"shares.{key} is a port number from 1 to 65535")
    return value


TELEMETRY_RANGES = {"export_seconds": (5, 3600)}
SERVICE_RX = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TOKEN_RX = re.compile(r"^[A-Za-z0-9._~-]+$")


def _telemetry_setting(key, value):
    if key == "prometheus_token":
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and 16 <= len(value.strip()) <= 200 and TOKEN_RX.match(value.strip())):
            raise ValueError(
                "telemetry.prometheus_token is 16 to 200 letters, digits, ., _, ~ and -, like the output of openssl rand -hex 32"
            )
        return value.strip()
    if key in ("enabled", "traces", "metrics", "prometheus"):
        if not isinstance(value, bool):
            raise ValueError(f"telemetry.{key} is true or false")
        return value
    if key == "grafana_url":
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("telemetry.grafana_url is Grafana's http(s) address, like http://localhost:3001")
        return value.strip().rstrip("/")
    if key == "endpoint":
        if value is None or value == "":
            return None
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("telemetry.endpoint is the OTLP/HTTP address of a collector, like http://localhost:4318")
        v = value.strip().rstrip("/")
        if v.endswith(("/v1/traces", "/v1/metrics")):
            raise ValueError("telemetry.endpoint is the collector's base address, without /v1/traces or /v1/metrics")
        try:
            u = urllib.parse.urlsplit(v)
            host = u.hostname or ""
            u.port  # noqa: B018 - ValueError for a port out of range
        except ValueError:
            raise ValueError("telemetry.endpoint isn't a valid address") from None
        if u.username is not None or u.password is not None:
            # the endpoint is stored and shown as it is; a token belongs in the sealed headers
            raise ValueError("telemetry.endpoint can't carry a user name or password; put credentials in telemetry.headers")
        try:
            link_local = ipaddress.ip_address(host).is_link_local
        except ValueError:
            link_local = False
        if link_local:
            raise ValueError("telemetry.endpoint can't be a link-local address (169.254.0.0/16, fe80::/10)")
        return v
    if key == "sample_ratio":
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1):
            raise ValueError("telemetry.sample_ratio is a number from 0 to 1")
        return float(value)
    if key == "service_name":
        if not (isinstance(value, str) and SERVICE_RX.match(value.strip())):
            raise ValueError("telemetry.service_name is up to 64 letters, digits, ., _ and -")
        return value.strip()
    if key == "prices":
        return _prices(value)
    lo, hi = TELEMETRY_RANGES[key]
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"telemetry.{key} is a whole number from {lo} to {hi}")
    return value


def _analytics_setting(key, value):
    from . import analytics

    if key == "enabled":
        if not isinstance(value, bool):
            raise ValueError("analytics.enabled is true or false")
        return value
    if key == "measurement_id":
        if value in (None, ""):
            return None
        v = value.strip().upper() if isinstance(value, str) else ""
        if not analytics.MEASUREMENT_RX.match(v):
            raise ValueError("analytics.measurement_id is a GA4 measurement id, like G-ABC123XYZ")
        return v
    if key == "events":
        if not (isinstance(value, list) and all(isinstance(x, str) for x in value)) or set(value) - set(analytics.EVENTS):
            raise ValueError(f"analytics.events is a list of: {', '.join(analytics.EVENTS)}")
        return [e for e in analytics.EVENTS if e in value]
    if key == "inactive_days":
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 365):
            raise ValueError("analytics.inactive_days is a whole number of days from 1 to 365")
        return value
    raise ValueError(f"unknown setting analytics.{key}")


def _origin(url):
    try:
        u = urllib.parse.urlsplit(url or "")
        return (u.scheme, u.hostname, u.port)
    except ValueError:
        return None


def _prices(value):
    """{model: price}: how each model's calls are costed (docs/activity.md#costs). By tokens, {input, output} USD per
    million tokens (unit "tokens", the default); by time, {unit: "time", per_hour} USD per hour a call takes (a local
    model on your own machine); or {unit: "off"}. A model left out isn't costed."""
    msg = (
        "telemetry.prices gives each model a price: {input, output} USD per million tokens, {unit: time, per_hour} "
        "USD per hour, or {unit: off}"
    )
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError(msg)

    def num(n):
        return isinstance(n, (int, float)) and not isinstance(n, bool) and 0 <= n <= 1_000_000

    out = {}
    for model, p in value.items():
        name = str(model).strip()
        if not name or len(name) > 200 or not isinstance(p, dict):
            raise ValueError(msg)
        unit = p.get("unit") or "tokens"
        if unit == "tokens":
            if set(p) - {"unit", "input", "output"}:
                raise ValueError(msg)
            nums = {k: p.get(k, 0) for k in ("input", "output")}
            if not all(num(n) for n in nums.values()):
                raise ValueError(msg)
            out[name] = {k: float(n) for k, n in nums.items()}  # the unit stays implicit, as prices saved before units
        elif unit == "time":
            if set(p) - {"unit", "per_hour"} or not num(p.get("per_hour", 0)):
                raise ValueError(msg)
            out[name] = {"unit": "time", "per_hour": float(p.get("per_hour", 0))}
        elif unit == "off":
            if set(p) - {"unit"}:
                raise ValueError(msg)
            out[name] = {"unit": "off"}
        else:
            raise ValueError(msg)
    return out


def _upload_setting(key, value):
    if key == "extensions":
        ok = isinstance(value, list) and value and all(isinstance(x, str) for x in value)
        exts = sorted({"." + x.strip().lower().lstrip(".") for x in value}) if ok else []
        if not exts or any(e not in UPLOAD_TYPES for e in exts):
            raise ValueError(
                f"uploads.extensions is a list of audio, video, document and image types from: {', '.join(sorted(UPLOAD_TYPES))}"
            )
        return exts
    lo, hi = UPLOAD_RANGES[key]
    if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
        raise ValueError(f"uploads.{key} is a whole number from {lo} to {hi}")
    return value


def save(db, base, section, changes, user=None):
    if section not in EDITABLE:
        raise ValueError(f"{section} can't be changed in the app")
    allowed, defaults = EDITABLE[section], store.DEFAULTS.get(section, {})
    row = db.one("SELECT data, sealed FROM $r", r=R("app_setting", section)) or {}
    data, sealed = _data(row), dict(row.get("sealed") or {})
    for k, v in (changes or {}).items():
        if allowed is not None and k not in allowed:
            raise ValueError(f"{section}.{k} can't be changed in the app")
        if k in SECRETS.get(section, ()):
            if v is None or v == "":
                sealed.pop(k, None)
            elif isinstance(v, str):
                if (section, k) == ("telemetry", "headers"):
                    telemetry.parse_headers(v)  # ValueError when malformed
                if section == "matterbridge" and (len(v) > 500 or "\n" in v.strip()):
                    raise ValueError(f"matterbridge.{k} is one line of text")
                if (section, k) in (
                    ("documents", "anytopdf_token"),
                    ("components", "hf_token"),
                    ("telemetry", "prometheus_token"),
                    ("telegram", "token"),
                ) or section == "slack":
                    v = _check(section, k, v, None)
                if (section, k) == ("https", "dns_credentials"):
                    from . import https

                    https.parse_credentials(v)
                sealed[k] = seal(base, v, f"setting:{section}.{k}")
            elif not (isinstance(v, dict) and v.get("secret")):  # the mask echoed back means "unchanged"
                raise ValueError(f"{section}.{k} must be text")
            continue
        if k not in defaults:
            raise ValueError(f"unknown setting {section}.{k}")
        before = data.get(k, (base.get(section) or {}).get(k))
        if (section, k) == ("documents", "web_networks") and isinstance(v, list):
            # the server's own networks are shown with the rest and stay whatever the web app sends: keep only the others
            server = {_network(x) for x in (base.get("documents") or {}).get("web_networks") or []}
            v = [x for x in v if _network(x) not in server]
        data[k] = _check(section, k, v, defaults[k])
        new_headers = isinstance((changes or {}).get("headers"), str)  # the mask echoed back is no new value
        if (section, k) == ("telemetry", "endpoint") and not new_headers and _origin(before) != _origin(data[k]):
            # the saved headers (an auth token) were for the old collector: they don't go to a new host
            if before:
                sealed.pop("headers", None)
    if section == "logs" and data.get("syslog") and not data.get("syslog_host", (base.get("logs") or {}).get("syslog_host")):
        raise ValueError("set the syslog server's address (logs.syslog_host) to send logs to it")
    if section == "speakers":
        m = data.get("match_threshold", defaults["match_threshold"])
        r = data.get("review_threshold", defaults["review_threshold"])
        if not (0 <= r <= m <= 1):
            raise ValueError("thresholds must satisfy 0 ≤ review ≤ match ≤ 1")
    if section == "tokens" and data.get("default_days", defaults["default_days"]) > data.get("max_days", defaults["max_days"]):
        raise ValueError("tokens.default_days can't be more than tokens.max_days")
    if section == "tokens" and data.get("oauth_refresh_days", defaults["oauth_refresh_days"]) > data.get("max_days", defaults["max_days"]):
        raise ValueError("tokens.oauth_refresh_days can't be more than tokens.max_days")
    if section == "auth" and data.get("passwords") is False and (changes or {}).get("passwords") is False:
        # nobody could sign in: every admin would need a password, and has none that works any more
        if not db.values(
            "SELECT VALUE id FROM passkey WHERE account IN (SELECT VALUE record::id(id) FROM account WHERE admin = true AND disabled != true) LIMIT 1"
        ):
            raise ValueError("add a passkey for an admin before turning passwords off, or nobody could administer Lens")
    if section == "backups" and data.get("connection"):
        from . import blobs

        blobs.check_connection(db, data["connection"])
    if section == "files" and data.get("store") == "connection":
        from . import blobs

        if not data.get("connection"):
            raise ValueError("choose the storage connection files are kept on")
        blobs.check_connection(db, data["connection"])
    if section == "search" and data.get("engine") == "opensearch":
        url = data.get("opensearch_url", (base.get("search") or {}).get("opensearch_url"))
        if not (isinstance(url, str) and re.match(r"https?://[^/\s]+", url)):
            raise ValueError("search.engine opensearch needs search.opensearch_url (http:// or https://)")
    if section == "tunnel":
        from . import tunnel

        merged = {**(base.get("tunnel") or {}), **data, **{k: "set" for k in sealed}}
        tunnel.check({"tunnel": merged})
    if section == "https":
        from . import https

        merged = {**(base.get("https") or {}), **data, **{k: "set" for k in sealed}}
        merged.pop("dns_credentials", None)  # its fields aren't needed to check the rest
        https.check({"https": merged})
    if (
        section == "server"
        and "allowed_hosts" in data
        and not (data["allowed_hosts"] and all(isinstance(h, str) and h for h in data["allowed_hosts"]))
    ):
        raise ValueError("server.allowed_hosts needs at least one host name")
    db.q(
        "UPSERT $r CONTENT $d",
        r=R("app_setting", section),
        d={"data": json.dumps(data), "sealed": sealed, "updated_at": store.now(), "updated_by": user},
    )
    db.next_id("settings")


FEDORA_RANGES = {"max_file_mb": (0, 1_000_000), "sync_seconds": (10, 86400), "full_hours": (1, 720)}


def _fedora_setting(key, value):
    if key in ("enabled", "files"):
        if not isinstance(value, bool):
            raise ValueError(f"fedora.{key} is true or false")
        return value
    if key in FEDORA_RANGES:
        lo, hi = FEDORA_RANGES[key]
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"fedora.{key} is a whole number from {lo} to {hi}")
        return value
    if key == "url":
        if value in (None, ""):
            return None
        u = urllib.parse.urlsplit(str(value))
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.query or u.fragment:
            raise ValueError("fedora.url is the address of Fedora's REST API, like http://fedora:8080/fcrepo/rest")
        return str(value).rstrip("/")
    if key == "root":
        if not (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value)):
            raise ValueError("fedora.root is a folder name: letters, digits, ., _ and -")
        return value
    if key in ("user", "password"):
        if value is not None and not (isinstance(value, str) and len(value) <= 500):
            raise ValueError(f"fedora.{key} is text")
        return value or None
    raise ValueError(f"unknown setting fedora.{key}")
