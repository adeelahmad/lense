"""Settings changed in the web app: stored in SurrealDB, with secrets encrypted at rest.

Precedence, lowest first: built-in defaults, archive.yaml, settings saved in the app, then the environment. The
environment only holds what can't live in the database it unlocks (the connection, ARCHIVE_SECRET_KEY) and a
break-glass override for allowed hosts (ARCHIVE_ALLOWED_HOSTS), so a bad setting can't lock everyone out.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import pathlib
import re
import secrets
import threading

from . import convert, embeddings, ipgroups, objects, store

R = store.R
EDITABLE = {
    "transcribe": None,
    "diarize": None,
    "speakers": None,
    "analysis": None,
    "llm": None,
    "graph": None,
    "search": ("stemming", "semantic", "semantic_weight"),  # the model's folder (semantic_model) is a startup setting
    "reports": None,
    "workers": None,
    "iiif": None,
    "ai": None,
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
    "analytics": None,
    # the LibreOffice and Chromium paths are startup settings only (the web app can't choose what the server runs)
    "documents": ("page_pixels", "thumb_pixels", "ocr_below_chars", "max_pages", "convert_seconds", "attachment_resources"),
}
SECRETS = {"llm": ("api_key",)}
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
}
ENV_OVERRIDES = {("server", "allowed_hosts"): "ARCHIVE_ALLOWED_HOSTS"}
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
    for (sec, key), env in ENV_OVERRIDES.items():
        if os.environ.get(env):
            cfg[sec][key] = [x.strip() for x in os.environ[env].split(",") if x.strip()]
    return cfg


class Settings:
    """The effective configuration, rebuilt only when someone saves a setting."""

    def __init__(self, db, base):
        self.db, self.base, self._cfg, self._ver, self._lock = db, base, None, None, threading.Lock()

    def current(self):
        v = self.db.values("SELECT VALUE n FROM $r", r=R("seq", "settings"))
        with self._lock:
            if self._cfg is None or v != self._ver:
                self._cfg, self._ver = effective(self.db, self.base), v
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
            "locked": [k for (s, k), env in ENV_OVERRIDES.items() if s == sec and os.environ.get(env)],
        }
    out["bootstrap"] = {
        "database": db.url,
        "data_dir": base["data_dir"],
        "secret_key": "ARCHIVE_SECRET_KEY" if os.environ.get("ARCHIVE_SECRET_KEY") else "data_dir/secret.key",
        "rclone": base["sources"].get("rclone") or "rclone on PATH",
        "local_roots": base["sources"].get("local_roots") or [],
        **convert.bootstrap(base),
        "yolox_model": objects.yolox_model(base) or "not found",
        "semantic_model": embeddings.status(effective(db, base)),
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
    if section == "uploads":
        return _upload_setting(key, value)
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
    if (section, key) == ("analytics", "retention_days"):
        if not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3650):
            raise ValueError("analytics.retention_days is a whole number of days from 1 to 3650")
        return value
    if (section, key) == ("search", "semantic"):
        if not isinstance(value, bool):
            raise ValueError("search.semantic is true or false")
        return value
    if (section, key) == ("search", "semantic_weight"):
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1):
            raise ValueError("search.semantic_weight is a number from 0 to 1")
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
                sealed[k] = seal(base, v, f"setting:{section}.{k}")
            elif not (isinstance(v, dict) and v.get("secret")):  # the mask echoed back means "unchanged"
                raise ValueError(f"{section}.{k} must be text")
            continue
        if k not in defaults:
            raise ValueError(f"unknown setting {section}.{k}")
        data[k] = _check(section, k, v, defaults[k])
    if section == "speakers":
        m = data.get("match_threshold", defaults["match_threshold"])
        r = data.get("review_threshold", defaults["review_threshold"])
        if not (0 <= r <= m <= 1):
            raise ValueError("thresholds must satisfy 0 ≤ review ≤ match ≤ 1")
    if section == "tokens" and data.get("default_days", defaults["default_days"]) > data.get("max_days", defaults["max_days"]):
        raise ValueError("tokens.default_days can't be more than tokens.max_days")
    if section == "tokens" and data.get("oauth_refresh_days", defaults["oauth_refresh_days"]) > data.get("max_days", defaults["max_days"]):
        raise ValueError("tokens.oauth_refresh_days can't be more than tokens.max_days")
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
