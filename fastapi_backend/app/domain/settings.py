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

from . import convert, ipgroups, objects, store, telemetry

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
    "mail": None,
    "bridge": None,
    "notifications": None,
    "telemetry": None,
    "encryption": None,
    "fedora": None,
    "tunnel": None,
    # bind is a startup setting only
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
    "server": ("embed_frame_ancestors", "max_upload_mb", "allowed_hosts", "session_hours", "secure_cookies", "trusted_proxies"),
    "uploads": None,
    "tokens": None,
    "auth": ("passwords",),
    # the LibreOffice and Chromium paths are startup settings only (the web app can't choose what the server runs)
    "documents": ("page_pixels", "thumb_pixels", "ocr_below_chars", "max_pages", "convert_seconds", "attachment_resources"),
}
SECRETS = {
    "llm": ("api_key",),
    "embeddings": ("api_key",),
    "decisions": ("api_key",),
    "voice": ("tts_api_key",),
    "mail": ("password",),
    "bridge": ("token",),
    "telemetry": ("headers",),
    "fedora": ("password",),
    "tunnel": ("token", "api_token"),
}
ENUMS = {
    ("transcribe", "engine"): {"sensevoice", "whisper", "mlx-whisper"},
    ("transcribe", "device"): {"auto", "cpu", "cuda", "mps"},
    ("diarize", "engine"): {"auto", "channels", "cluster", "pyannote", "none"},
    ("speakers", "embedder"): {"speechbrain", "none"},
    ("speakers", "cross_namespace"): {"suggest", "off"},
    ("analysis", "entities"): {"rules", "spacy"},
    ("search", "stemming"): {"english", "none"},
    ("reports", "audio"): {"link", "embed", "none"},
    ("video", "ocr_engine"): {"auto", "tesseract", "apple-vision", "rapidocr", "doctr", "none"},
    ("video", "face_engine"): {"opencv", "insightface", "none"},
    ("video", "object_engine"): {"yolox", "ultralytics", "off"},
    ("decisions", "engine"): {"auto", "jev", "llm", "off"},
    ("voice", "input"): {"auto", "server", "browser"},
    ("mail", "security"): {"starttls", "ssl", "none"},
    ("bridge", "answer"): {"mention", "all"},
    ("tunnel", "mode"): {"off", "quick", "token", "managed"},
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
    ("fedora", "enabled"): "LENS_FEDORA",
    ("fedora", "url"): "LENS_FEDORA_URL",
    ("fedora", "user"): "LENS_FEDORA_USER",
    ("fedora", "password"): "LENS_FEDORA_PASSWORD",
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
# images.
UPLOAD_TYPES = frozenset([*store.MEDIA_EXT, ".aif", ".aiff", ".wma", ".mpg", ".mpeg", ".3gp", *store.DOCUMENT_EXT, *store.IMAGE_EXT])
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
    if section == "embeddings":
        return _embed_setting(key, value)
    if section == "uploads":
        return _upload_setting(key, value)
    if section == "notifications":
        return _notify_setting(key, value)
    if section == "telemetry":
        return _telemetry_setting(key, value)
    if section == "fedora":
        return _fedora_setting(key, value)
    if section == "sensors":
        return _sensor_setting(key, value)
    if section == "components":
        return _component_setting(key, value)
    if section == "mail" and key != "security":
        return _mail_setting(key, value)
    if section == "bridge" and key not in ("answer", "enabled"):
        return _bridge_setting(key, value)
    if (section, key) == ("voice", "tts_base_url"):
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and VIEWER_URL.match(value.strip())):
            raise ValueError("voice.tts_base_url is the http(s) address of an OpenAI-compatible server with /audio/speech")
        return value.strip().rstrip("/")
    if section == "voice" and key in ("tts_model", "tts_voice", "tts_api_key"):
        if value in (None, ""):
            return None
        if not (isinstance(value, str) and len(value.strip()) <= 500):
            raise ValueError(f"voice.{key} is text")
        return value.strip()
    if section == "decisions" and key != "engine":
        return _decision_setting(key, value)
    if (section, key) == ("documents", "attachment_resources"):
        if not isinstance(value, bool):
            raise ValueError("documents.attachment_resources is true or false")
        return value
    if section == "documents":
        lo, hi = DOCUMENT_RANGES[key]
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"documents.{key} is a whole number from {lo} to {hi}")
        return value
    if (section, key) == ("video", "object_min_score"):
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0.05 <= value <= 0.95):
            raise ValueError("video.object_min_score is a number from 0.05 to 0.95")
        return float(value)
    if (section, key) == ("tokens", "oauth_access_minutes"):
        lo, hi = OAUTH_ACCESS_MINUTES
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"tokens.oauth_access_minutes is a whole number of minutes from {lo} to {hi}")
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


def _bridge_setting(key, value):
    if key == "poll_seconds":
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 300):
            raise ValueError("bridge.poll_seconds is a whole number of seconds from 1 to 300")
        return value
    if key == "users":
        if not (isinstance(value, list) and all(isinstance(v, str) and len(v) <= 200 for v in value) and len(value) <= 500):
            raise ValueError("bridge.users is a list of chat usernames")
        return list(dict.fromkeys(v.strip() for v in value if v.strip()))
    if key not in ("url", "token", "gateway", "account", "name"):
        raise ValueError(f"unknown setting bridge.{key}")
    if value in (None, ""):
        return "Lens" if key == "name" else None
    if not (isinstance(value, str) and len(value.strip()) <= 500):
        raise ValueError(f"bridge.{key} is text")
    value = value.strip() if key != "token" else value
    if key == "url":
        u = urllib.parse.urlsplit(value)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("bridge.url is the address of Matterbridge's API, such as http://matterbridge:4242")
        return value.rstrip("/")
    if key == "account":
        if not EMAIL_RX.match(value):
            raise ValueError("bridge.account is the email of the Lens account it answers as")
        return value.lower()
    if key == "name" and not re.match(r"^[\w .-]{1,40}$", value):
        raise ValueError("bridge.name is a short name, such as Lens")
    return value


def _component_setting(key, value):
    from . import components

    if key == "auto":
        if not isinstance(value, bool):
            raise ValueError("components.auto is true or false")
        return value
    if key == "also":
        known = {c.id for c in components.COMPONENTS if c.kind != "program"}
        if not (isinstance(value, list) and all(isinstance(v, str) and v in known for v in value)):
            raise ValueError("components.also lists components to fetch: " + ", ".join(sorted(known)))
        return list(dict.fromkeys(value))
    raise ValueError(f"unknown setting components.{key}")


def _decision_setting(key, value):
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
    raise ValueError(f"unknown setting decisions.{key}")


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


TELEMETRY_RANGES = {"export_seconds": (5, 3600)}
SERVICE_RX = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _telemetry_setting(key, value):
    if key in ("enabled", "traces", "metrics"):
        if not isinstance(value, bool):
            raise ValueError(f"telemetry.{key} is true or false")
        return value
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


def _origin(url):
    try:
        u = urllib.parse.urlsplit(url or "")
        return (u.scheme, u.hostname, u.port)
    except ValueError:
        return None


def _prices(value):
    """{model: {input, output}}: USD per million tokens, for the cost estimates."""
    msg = "telemetry.prices gives each model its input and output price in USD per million tokens"
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError(msg)
    out = {}
    for model, p in value.items():
        name = str(model).strip()
        if not name or len(name) > 200 or not isinstance(p, dict) or set(p) - {"input", "output"}:
            raise ValueError(msg)
        nums = {k: p.get(k, 0) for k in ("input", "output")}
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and 0 <= n <= 1_000_000 for n in nums.values()):
            raise ValueError(msg)
        out[name] = {k: float(n) for k, n in nums.items()}
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
                sealed[k] = seal(base, v, f"setting:{section}.{k}")
            elif not (isinstance(v, dict) and v.get("secret")):  # the mask echoed back means "unchanged"
                raise ValueError(f"{section}.{k} must be text")
            continue
        if k not in defaults:
            raise ValueError(f"unknown setting {section}.{k}")
        before = data.get(k, (base.get(section) or {}).get(k))
        data[k] = _check(section, k, v, defaults[k])
        if (section, k) == ("telemetry", "endpoint") and "headers" not in (changes or {}) and _origin(before) != _origin(data[k]):
            # the saved headers (an auth token) were for the old collector: they don't go to a new host
            if before:
                sealed.pop("headers", None)
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
    if section == "tunnel":
        from . import tunnel

        merged = {**(base.get("tunnel") or {}), **data, **{k: "set" for k in sealed}}
        tunnel.check({"tunnel": merged})
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
