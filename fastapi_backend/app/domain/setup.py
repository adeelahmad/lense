"""First-run setup: what a fresh install still needs, from the environment (.env) or the web wizard.

A fresh install (no accounts when the API first starts) is marked with setup:wizard. The web app then walks the first
admin through the rest (the first namespace, the model provider, storage) until they finish or skip it; installs that
already had accounts never see the wizard. Whatever the environment sets is applied at startup and wins: the wizard
shows those fields locked.

  LENS_ADMIN_EMAIL, LENS_ADMIN_NAME                        the first admin, created at startup (no setup code needed);
                                                           the log prints a link for adding their passkey
  LENS_ADMIN_PASSWORD                                      ... with a password instead (passwords stay on)
  LENS_NAMESPACE                                           the first namespace, created at startup while there is none
  LENS_LLM_BASE_URL, LENS_LLM_MODEL, LENS_LLM_API_KEY      the model provider (settings.ENV_OVERRIDES)
  LENS_TELEMETRY, LENS_TELEMETRY_ENDPOINT                  opt-in telemetry (off unless set on; docs/telemetry.md)
  LENS_SETUP_SETTINGS                                      settings to start with, {section: {key: value}} as JSON (or
                                                           base64url JSON): saved once, as if made in the app, so they
                                                           stay editable there (the web installer's answers)
  LENS_SETUP_WIZARD=off                                    never show the wizard (everything comes from .env)

The first admin still needs the one-time setup code (or the environment), so a stranger can't claim a public server.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import auth, settings, sources, store

R = store.R
WIZARD = ("setup", "wizard")
SEEDED = ("setup", "settings")  # LENS_SETUP_SETTINGS was applied: once, so changes made in the app later stay
DEFAULT_NAMESPACE = "archive"  # what the wizard suggests too
log = logging.getLogger("lens")


def _off():
    return os.environ.get("LENS_SETUP_WIZARD", "").strip().lower() in ("off", "false", "0", "no", "done")


def pending(db):
    """Whether the web wizard is still to be finished."""
    if _off():
        return False
    row = db.one("SELECT done_at FROM $r", r=R(*WIZARD))
    return bool(row) and not row.get("done_at")


def mark_fresh(db, cfg=None):
    """Called at startup while no accounts exist: this install gets the wizard (once; finishing it is kept). A new
    archive (nothing in it yet) also encrypts the files it keeps from the start (docs/encryption.md); one that has
    files already keeps its setting, and `lens encrypt` converts it."""
    if not db.one("SELECT id FROM $r", r=R(*WIZARD)):
        db.q("UPSERT $r CONTENT $d", r=R(*WIZARD), d={"created_at": store.now()})
        empty = not db.values("SELECT VALUE id FROM recording LIMIT 1")
        if cfg is not None and empty and not db.one("SELECT id FROM $r", r=R("app_setting", "encryption")):
            settings.save(db, cfg, "encryption", {"files": True}, user="setup")


def finish(db, user=None, skipped=False):
    """Finish (or skip) the wizard. An archive still without a namespace gets the default one, so importing, watching
    folders and the graph have somewhere to go without asking first. Returns its name when it made one."""
    db.q("UPDATE $r SET done_at = $t, done_by = $u, skipped = $s", r=R(*WIZARD), t=store.now(), u=user, s=skipped)
    if store.space_names(db):
        return None
    store.ns_id(db, DEFAULT_NAMESPACE)
    return DEFAULT_NAMESPACE


def env_admin():
    email, password = os.environ.get("LENS_ADMIN_EMAIL", "").strip(), os.environ.get("LENS_ADMIN_PASSWORD", "")
    return (email, password or None, os.environ.get("LENS_ADMIN_NAME", "").strip() or None) if email else None


def env_namespace():
    return os.environ.get("LENS_NAMESPACE", "").strip().lower() or None


def env_settings():
    """LENS_SETUP_SETTINGS parsed: {section: {key: value}}, or {} when unset or unreadable."""
    raw = os.environ.get("LENS_SETUP_SETTINGS", "").strip()
    if not raw:
        return {}
    try:
        if not raw.startswith("{"):
            raw = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        data = json.loads(raw)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        log.error("LENS_SETUP_SETTINGS isn't JSON (or base64url JSON); its settings weren't applied")
        return {}
    if not isinstance(data, dict):
        log.error("LENS_SETUP_SETTINGS is {section: {key: value}}; its settings weren't applied")
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v}


def apply_settings(db, cfg):
    """Save LENS_SETUP_SETTINGS once, a section at a time (one that doesn't check out is logged and left out). Returns
    the sections saved."""
    wanted = env_settings()
    if not wanted or db.one("SELECT id FROM $r", r=R(*SEEDED)):
        return []
    saved = []
    for section, values in wanted.items():
        keep = {k: v for k, v in values.items() if k not in settings.locked(section)}  # the environment wins anyway
        if not keep:
            continue
        try:
            settings.save(db, cfg, section, keep, user="install")
        except (ValueError, TypeError) as e:
            log.error("LENS_SETUP_SETTINGS %s: %s; set it in Settings instead", section, e)
        else:
            saved.append(section)
    db.q("UPSERT $r CONTENT $d", r=R(*SEEDED), d={"applied_at": store.now(), "sections": saved})
    if saved:
        log.info("Applied the installer's settings: %s", ", ".join(saved))
    return saved


def apply_env(db, cfg=None):
    """Create what the environment names while the archive has none: the first admin, the first namespace. Only then,
    so one deleted later doesn't come back on the next start. LENS_SETUP_SETTINGS is saved once too."""
    if cfg is not None:
        apply_settings(db, cfg)
    admin = env_admin()
    if admin and auth.account_count(db) == 0:
        try:
            uid = auth.create_account(db, admin[0], admin[1], admin[2], admin=True)
        except ValueError as e:
            log.error("LENS_ADMIN_EMAIL / LENS_ADMIN_PASSWORD: %s; create the first admin in the web app instead", e)
        else:
            auth.audit(db, {"id": uid, "email": admin[0]}, "setup", detail=["environment"])
            log.info("Created the first admin %s from the environment", admin[0])
            if not admin[1]:
                from . import passkeys

                raw = passkeys.create_link(db, uid)
                log.warning("Add the first admin's passkey at %s", passkeys.link_url(raw))
    name = env_namespace()
    if name and not store.space_names(db):
        if not store.NS_RX.match(name):
            log.error("LENS_NAMESPACE %r: namespace names use lowercase letters, digits, - and _", name)
        else:
            store.ns_id(db, name)


def view(db, cfg):
    """The wizard's starting point: what is set already, and which fields the environment or archive.yaml locks."""
    shown = settings.effective(db, cfg, reveal=False)
    llm = {k: shown["llm"].get(k) for k in ("base_url", "model", "api_key", "vision_model")}
    if not isinstance(llm["api_key"], dict):
        llm["api_key"] = {"secret": True, "set": False}
    names = [r["name"] for r in db.rows("SELECT name FROM space ORDER BY name")]
    admin = env_admin()
    return {
        "pending": pending(db),
        "admin": {"from_env": bool(admin)},
        "namespace": {
            "existing": names,
            # archive.yaml or LENS_NAMESPACE named the namespaces: the wizard shows them instead of asking
            "locked": bool(env_namespace() or cfg["namespaces"]),
        },
        "llm": {"values": llm, "locked": settings.locked("llm")},
        "storage": {
            "data_dir": cfg["data_dir"],
            "database": db.url,
            "embedded": db.embedded,
            "local_roots": cfg["sources"].get("local_roots") or [],
            "max_upload_mb": shown["uploads"]["max_mb"],
            "watches": len(db.values("SELECT VALUE id FROM watch_path")),
        },
        "telemetry": {
            "enabled": bool(shown["telemetry"].get("enabled")),
            "endpoint": shown["telemetry"].get("endpoint"),
            "locked": [k for k in settings.locked("telemetry") if k in ("enabled", "endpoint")],
        },
        "oauth": {
            "enabled": bool(shown["tokens"].get("oauth_enabled", True)),
            "access_minutes": int(shown["tokens"]["oauth_access_minutes"]),
            "refresh_days": int(shown["tokens"]["oauth_refresh_days"]),
        },
    }


def save_namespace(db, name, graph="shared"):
    name = (name or "").strip().lower()
    if not store.NS_RX.match(name):
        raise ValueError("namespace names use lowercase letters, digits, - and _")
    if graph not in ("shared", "isolated"):
        raise ValueError("graph is shared or isolated")
    sid = store.ns_id(db, name)
    db.q("UPDATE $r SET graph = $g", r=R("space", sid), g=graph)
    return sid


def save_llm(db, cfg, values, user=None):
    """Save the model provider, leaving out what the environment sets (it would win anyway)."""
    keep = {k: v for k, v in values.items() if k not in settings.locked("llm")}
    if keep:
        settings.save(db, cfg, "llm", keep, user)
    return sorted(keep)


def save_telemetry(db, cfg, enabled, endpoint, user=None):
    """Opt in to telemetry (or stay out), leaving out what the environment sets. On needs an endpoint."""
    locked = settings.locked("telemetry")
    endpoint = (endpoint or "").strip() or None
    if enabled and not endpoint and "endpoint" not in locked:
        raise ValueError("telemetry needs an endpoint to send to, like http://localhost:4318")
    values = {"enabled": bool(enabled), **({"endpoint": endpoint} if endpoint or enabled else {})}  # off keeps the address
    keep = {k: v for k, v in values.items() if k not in locked}
    if keep:
        settings.save(db, cfg, "telemetry", keep, user)
    return sorted(keep)


def save_oauth(db, cfg, enabled, access_minutes=None, refresh_days=None, user=None):
    """Whether apps and MCP clients may sign people in through OAuth, and how long their tokens last."""
    values = {"oauth_enabled": bool(enabled)}
    if access_minutes is not None:
        values["oauth_access_minutes"] = access_minutes
    if refresh_days is not None:
        values["oauth_refresh_days"] = refresh_days
    settings.save(db, cfg, "tokens", values, user)
    return sorted(values)


def save_storage(db, cfg, max_upload_mb=None, folder=None, namespace=None, user=None):
    """The upload limit, and optionally a folder on this machine (inside sources.local_roots) watched into a
    namespace. Returns the watch's id, if one was made."""
    if max_upload_mb is not None:
        settings.save(db, cfg, "uploads", {"max_mb": max_upload_mb}, user)
    if not folder:
        return None
    if not namespace:
        raise ValueError("choose the namespace the folder's files go into")
    sid = store.ns_id(db, namespace, create=False)
    src = next((s["id"] for s in sources.list_sources(db) if s.get("type") == "local"), None)
    if src is None:
        src = sources.create(db, cfg, "Folders on this machine", "local", {}, {}, user)
    return sources.create_watch(db, cfg, src, folder, sid, user)


# Cloud providers with an OpenAI-compatible API: picking one fills in its address and a model, so only the key is
# pasted. `models` are suggestions (the model stays editable); `key_url` is where the provider hands out keys.
CLOUD_PROVIDERS = (
    {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5-mini",
        "models": ["gpt-5-mini", "gpt-5", "gpt-5-nano", "gpt-4.1-mini"],
        "key_url": "https://platform.openai.com/api-keys",
    },
    {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-opus-5-5",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-flash-latest",
        "models": ["gemini-flash-latest", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
        "key_url": "https://aistudio.google.com/apikey",
    },
    {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openrouter/auto",
        "models": ["openrouter/auto", "openai/gpt-5-mini", "google/gemini-2.5-flash", "meta-llama/llama-3.3-70b-instruct"],
        "key_url": "https://openrouter.ai/keys",
    },
    {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b"],
        "key_url": "https://console.groq.com/keys",
    },
    {
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "models": ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest"],
        "key_url": "https://console.mistral.ai/api-keys",
    },
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_url": "https://platform.deepseek.com/api_keys",
    },
)


# Model servers people run themselves, on the ports they listen on by default. Looked for on this machine, on the
# Docker host (host.docker.internal; the compose files map it on Linux too) and as a compose service named ollama.
LOCAL_SERVERS = (("Ollama", 11434), ("LM Studio", 1234), ("llama.cpp", 8080), ("vLLM", 8000), ("LocalAI", 8081))
LOCAL_HOSTS = ("localhost", "host.docker.internal", "ollama")
NOT_CHAT = ("embed", "rerank", "whisper", "tts", "clip", "bge-", "minilm")


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local addresses: never through a proxy


def _models_at(base_url, timeout):
    try:
        with _DIRECT.open(base_url + "/models", timeout=timeout) as r:
            data = json.load(r).get("data")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError):
        return None
    if not isinstance(data, list):
        return None
    return sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})


def detect_llm(timeout=1.5, hosts=None, servers=None):
    """OpenAI-compatible model servers that answer on their usual ports, with their models: what the wizard offers
    so nobody has to type an address. One entry per server (the first address that reaches it), chat models first,
    with the one to suggest."""
    tries = [(kind, f"http://{h}:{port}/v1") for kind, port in servers or LOCAL_SERVERS for h in hosts or LOCAL_HOSTS]
    with ThreadPoolExecutor(max_workers=len(tries)) as pool:
        answers = list(pool.map(lambda t: _models_at(t[1], timeout), tries))
    found, seen = [], set()
    for (kind, url), models in zip(tries, answers, strict=True):
        if not models or (kind, tuple(models)) in seen:
            continue
        seen.add((kind, tuple(models)))
        chat = [m for m in models if not any(w in m.lower() for w in NOT_CHAT)]
        found.append(
            {"kind": kind, "base_url": url, "models": chat + [m for m in models if m not in chat], "suggested": (chat or models)[0]}
        )
    return found
